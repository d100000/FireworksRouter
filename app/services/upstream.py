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


async def register_key_quick(
    session: AsyncSession,
    plaintext: str,
    name: str | None = None,
    notes: str | None = None,
    priority: int = 0,
    weight: int = 100,
) -> RegisterResult:
    """快速入库：只做加密 + 去重 + DB 写入，不调用上游 API。

    入库后返回 status=testing 状态。后续应调用 probe_after_register 异步更新状态。
    用于批量导入场景，避免串行调用 Fireworks 导致前端 timeout。
    """
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
    return RegisterResult(key=record, created=True, note="pending_probe")


async def probe_after_register(key_id: int) -> None:
    """在独立 session 中跑一次完整探针 + 状态决策。

    设计上独立运行（asyncio.create_task），允许批量导入快速返回。
    异常静默吞掉，保留 status=testing/unhealthy 让定时探针下次重试。
    """
    from app.db import session_scope

    try:
        async with session_scope() as session:
            record = await session.get(UpstreamKey, key_id)
            if record is None:
                return
            plaintext = decrypt_key(record.key_encrypted)

            # 1. list_accounts
            try:
                accounts = await fw.list_accounts(plaintext)
                if not accounts:
                    record.status = UpstreamKeyStatus.auto_disabled
                    record.auto_disable_reason = "no_accessible_account"
                    record.disabled_at = datetime.now(timezone.utc)
                    return
                primary = accounts[0]
                record.account_id = primary.account_id
                record.account_email = primary.email
                record.account_state = primary.state
                record.suspend_state = primary.suspend_state
            except Exception as e:  # noqa: BLE001
                logger.warning("probe_after_register #{} list_accounts failed: {}", key_id, e)
                record.status = UpstreamKeyStatus.unhealthy
                record.auto_disable_reason = f"list_accounts_error: {str(e)[:200]}"
                record.disabled_at = datetime.now(timezone.utc)
                return

            # 2. list_quotas
            try:
                snap = await fw.list_quotas(plaintext, record.account_id)
                record.monthly_spend_limit_usd = snap.monthly_spend_limit_usd
                record.monthly_spend_used_usd = snap.monthly_spend_used_usd
                record.balance_usd = snap.monthly_spend_remaining_usd
                if record.rpm_limit == 0 and snap.serverless_rpm > 0:
                    record.rpm_limit = snap.serverless_rpm
                record.balance_updated_at = datetime.now(timezone.utc)
            except Exception as e:  # noqa: BLE001
                logger.warning("probe_after_register #{} list_quotas failed: {}", key_id, e)

            # 3. 状态决策
            if record.suspend_state and record.suspend_state.upper() != "UNSUSPENDED":
                record.status = UpstreamKeyStatus.auto_disabled
                record.auto_disable_reason = f"suspend_state={record.suspend_state}"
                record.disabled_at = datetime.now(timezone.utc)
            else:
                record.status = UpstreamKeyStatus.active
    except Exception as e:  # noqa: BLE001
        logger.exception("probe_after_register #{} fatal: {}", key_id, e)


async def register_key(
    session: AsyncSession,
    plaintext: str,
    name: str | None = None,
    notes: str | None = None,
    priority: int = 0,
    weight: int = 100,
) -> RegisterResult:
    """单条添加：入库 + 同步跑一次探针（保持原有阻塞语义）。

    适合 UI 单条添加场景；批量导入请用 register_key_quick + probe_after_register。
    """
    result = await register_key_quick(
        session, plaintext, name=name, notes=notes, priority=priority, weight=weight,
    )
    if not result.created:
        return result
    # 同步跑探针（必须先 commit 当前 session 让 probe 能查到记录）
    await session.commit()
    await probe_after_register(result.key.id)
    # 重新加载记录的最新状态
    await session.refresh(result.key)
    return result


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
