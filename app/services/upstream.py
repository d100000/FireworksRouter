from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import decrypt_key, encrypt_key, hash_key, preview_key
from app.models import UpstreamKey, UpstreamKeyStatus
from app.services import fireworks as fw
from app.utils.logger import logger


@dataclass
class RegisterResult:
    key: UpstreamKey
    created: bool
    note: str | None = None


async def get_decrypted_key(record: UpstreamKey) -> str:
    return decrypt_key(record.key_encrypted)


async def find_by_hash(session: AsyncSession, plaintext: str) -> UpstreamKey | None:
    stmt = select(UpstreamKey).where(UpstreamKey.key_hash == hash_key(plaintext))
    return (await session.execute(stmt)).scalar_one_or_none()


async def register_key(
    session: AsyncSession,
    plaintext: str,
    name: str | None = None,
    notes: str | None = None,
    priority: int = 0,
    weight: int = 100,
) -> RegisterResult:
    plaintext = plaintext.strip()
    if not plaintext.startswith("fw_"):
        raise ValueError("Fireworks key must start with 'fw_'")

    existing = await find_by_hash(session, plaintext)
    if existing is not None:
        return RegisterResult(key=existing, created=False, note="duplicate")

    record = UpstreamKey(
        name=name or preview_key(plaintext),
        key_encrypted=encrypt_key(plaintext),
        key_hash=hash_key(plaintext),
        key_preview=preview_key(plaintext),
        status=UpstreamKeyStatus.testing,
        priority=priority,
        weight=weight,
        notes=notes,
    )
    session.add(record)
    await session.flush()

    # 1. 探测 account_id
    try:
        accounts = await fw.list_accounts(plaintext)
        if not accounts:
            record.status = UpstreamKeyStatus.auto_disabled
            record.auto_disable_reason = "no_accessible_account"
            record.disabled_at = datetime.now(timezone.utc)
            await session.flush()
            return RegisterResult(key=record, created=True, note=record.auto_disable_reason)
        primary = accounts[0]
        record.account_id = primary.account_id
        record.account_email = primary.email
        record.account_state = primary.state
        record.suspend_state = primary.suspend_state
    except Exception as e:  # noqa: BLE001
        logger.exception("register_key: list_accounts failed: {}", e)
        record.status = UpstreamKeyStatus.unhealthy
        record.auto_disable_reason = f"list_accounts_error: {e}"
        record.disabled_at = datetime.now(timezone.utc)
        await session.flush()
        return RegisterResult(key=record, created=True, note=record.auto_disable_reason)

    # 2. 探测余额
    try:
        snap = await fw.list_quotas(plaintext, record.account_id)
        record.monthly_spend_limit_usd = snap.monthly_spend_limit_usd
        record.monthly_spend_used_usd = snap.monthly_spend_used_usd
        record.balance_usd = snap.monthly_spend_remaining_usd
        if record.rpm_limit == 0 and snap.serverless_rpm > 0:
            record.rpm_limit = snap.serverless_rpm
        record.balance_updated_at = datetime.now(timezone.utc)
    except Exception as e:  # noqa: BLE001
        logger.warning("register_key: list_quotas failed: {}", e)

    # 3. 状态决策
    if record.suspend_state and record.suspend_state.upper() != "UNSUSPENDED":
        record.status = UpstreamKeyStatus.auto_disabled
        record.auto_disable_reason = f"suspend_state={record.suspend_state}"
        record.disabled_at = datetime.now(timezone.utc)
    else:
        record.status = UpstreamKeyStatus.active

    await session.flush()
    return RegisterResult(key=record, created=True, note=None)


async def set_enabled(
    session: AsyncSession, key_id: int, enabled: bool
) -> UpstreamKey | None:
    record = await session.get(UpstreamKey, key_id)
    if record is None:
        return None
    record.enabled = enabled
    if not enabled:
        record.status = UpstreamKeyStatus.disabled
        record.disabled_at = datetime.now(timezone.utc)
    else:
        record.status = UpstreamKeyStatus.active
        record.disabled_at = None
        record.auto_disable_reason = None
    await session.flush()
    return record


async def delete_key(session: AsyncSession, key_id: int) -> bool:
    record = await session.get(UpstreamKey, key_id)
    if record is None:
        return False
    await session.delete(record)
    await session.flush()
    return True


async def list_active(session: AsyncSession) -> list[UpstreamKey]:
    stmt = (
        select(UpstreamKey)
        .where(UpstreamKey.enabled.is_(True))
        .where(UpstreamKey.status == UpstreamKeyStatus.active)
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_all(session: AsyncSession) -> list[UpstreamKey]:
    stmt = select(UpstreamKey).order_by(UpstreamKey.id.desc())
    return list((await session.execute(stmt)).scalars().all())
