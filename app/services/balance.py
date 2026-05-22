from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crypto import decrypt_key
from app.db import session_scope
from app.models import ProbeHistory, UpstreamKey, UpstreamKeyStatus
from app.services import fireworks as fw
from app.utils.logger import logger

settings = get_settings()


@dataclass
class ProbeResult:
    key_id: int
    ok: bool
    balance_usd: float = 0.0
    suspend_state: str | None = None
    account_state: str | None = None
    error: str | None = None
    latency_ms: int = 0
    new_status: UpstreamKeyStatus | None = None
    disable_reason: str | None = None


async def _probe_single(record: UpstreamKey) -> ProbeResult:
    started = time.perf_counter()
    plaintext = decrypt_key(record.key_encrypted)

    result = ProbeResult(key_id=record.id, ok=False)
    try:
        if not record.account_id:
            accounts = await fw.list_accounts(plaintext)
            if not accounts:
                result.error = "no_accessible_account"
                result.new_status = UpstreamKeyStatus.auto_disabled
                result.disable_reason = "no_accessible_account"
                return result
            record.account_id = accounts[0].account_id
            record.account_email = accounts[0].email

        account = await fw.get_account(plaintext, record.account_id)
        result.suspend_state = account.suspend_state
        result.account_state = account.state

        snap = await fw.list_quotas(plaintext, record.account_id)
        result.balance_usd = snap.monthly_spend_remaining_usd

        # 状态决策
        if account.suspend_state and account.suspend_state.upper() != "UNSUSPENDED":
            result.new_status = UpstreamKeyStatus.auto_disabled
            result.disable_reason = f"suspend_state={account.suspend_state}"
        elif result.balance_usd < settings.probe_min_balance_usd:
            result.new_status = UpstreamKeyStatus.auto_disabled
            result.disable_reason = (
                f"low_balance={result.balance_usd:.4f} < {settings.probe_min_balance_usd}"
            )
        else:
            result.new_status = UpstreamKeyStatus.active

        # 写回字段
        record.suspend_state = account.suspend_state
        record.account_state = account.state
        record.monthly_spend_limit_usd = snap.monthly_spend_limit_usd
        record.monthly_spend_used_usd = snap.monthly_spend_used_usd
        record.balance_usd = result.balance_usd
        record.balance_updated_at = datetime.now(timezone.utc)
        result.ok = True
    except fw.FireworksError as e:
        result.error = str(e)
        if e.status in (401, 403):
            result.new_status = UpstreamKeyStatus.auto_disabled
            result.disable_reason = f"auth_failed_http_{e.status}"
        else:
            result.new_status = UpstreamKeyStatus.unhealthy
            result.disable_reason = f"probe_http_{e.status}"
    except Exception as e:  # noqa: BLE001
        result.error = str(e)
        result.new_status = UpstreamKeyStatus.unhealthy
        result.disable_reason = f"probe_exception: {e!r}"

    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result


def _apply_result(record: UpstreamKey, result: ProbeResult) -> None:
    if result.new_status is None:
        return
    record.status = result.new_status
    if result.new_status == UpstreamKeyStatus.auto_disabled:
        record.auto_disable_reason = result.disable_reason
        if record.disabled_at is None:
            record.disabled_at = datetime.now(timezone.utc)
    elif result.new_status == UpstreamKeyStatus.active:
        record.auto_disable_reason = None
        record.disabled_at = None


async def probe_one(key_id: int) -> ProbeResult | None:
    async with session_scope() as session:
        record = await session.get(UpstreamKey, key_id)
        if record is None:
            return None
        result = await _probe_single(record)
        _apply_result(record, result)
        session.add(
            ProbeHistory(
                upstream_key_id=record.id,
                upstream_key_preview=record.key_preview,
                success="ok" if result.ok else "error",
                balance_usd=result.balance_usd,
                monthly_spend_limit_usd=record.monthly_spend_limit_usd,
                monthly_spend_used_usd=record.monthly_spend_used_usd,
                suspend_state=result.suspend_state,
                account_state=result.account_state,
                error_message=result.error,
                latency_ms=result.latency_ms,
            )
        )
        return result


async def _probe_in_isolated_session(key_id: int, sem: asyncio.Semaphore) -> ProbeResult | None:
    async with sem:
        try:
            return await probe_one(key_id)
        except Exception as e:  # noqa: BLE001
            logger.exception("probe_one(key_id={}) failed: {}", key_id, e)
            return None


async def run_probe_round() -> dict[str, int]:
    started = time.perf_counter()
    async with session_scope() as session:
        ids = await _select_probe_targets(session)

    if not ids:
        logger.info("probe round: no targets")
        return {"total": 0, "ok": 0, "fail": 0, "ms": 0}

    sem = asyncio.Semaphore(max(1, settings.probe_concurrency))
    results = await asyncio.gather(*(_probe_in_isolated_session(i, sem) for i in ids))

    ok = sum(1 for r in results if r and r.ok)
    fail = sum(1 for r in results if r is None or not r.ok)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "probe round done: total={} ok={} fail={} elapsed={}ms",
        len(ids), ok, fail, elapsed_ms,
    )
    return {"total": len(ids), "ok": ok, "fail": fail, "ms": elapsed_ms}


async def _select_probe_targets(session: AsyncSession) -> list[int]:
    stmt = select(UpstreamKey.id).where(
        UpstreamKey.status.in_(
            [
                UpstreamKeyStatus.active,
                UpstreamKeyStatus.unhealthy,
                UpstreamKeyStatus.auto_disabled,
                UpstreamKeyStatus.testing,
            ]
        )
    )
    return list((await session.execute(stmt)).scalars().all())
