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
    # 新增：探针拿到的 limit/used，用于回写 record 与 ProbeHistory
    monthly_spend_limit_usd: float = 0.0
    monthly_spend_used_usd: float = 0.0
    suspend_state: str | None = None
    account_state: str | None = None
    # 新增：探针首次发现的 account_id / email（record 原本为 None 时填）
    discovered_account_id: str | None = None
    discovered_account_email: str | None = None
    error: str | None = None
    latency_ms: int = 0
    new_status: UpstreamKeyStatus | None = None
    disable_reason: str | None = None


async def _probe_http_only(
    key_id: int,
    plaintext: str,
    existing_account_id: str | None,
) -> ProbeResult:
    """纯 HTTP 阶段：调 Fireworks 控制面拿 account + quota，不持有任何 DB session。

    被 probe_one 在「关掉读 session」和「打开写 session」之间调用。
    所有 IO 都在这里发生，连接池不会被一个长 HTTP 卡住。
    """
    started = time.perf_counter()
    result = ProbeResult(key_id=key_id, ok=False)
    try:
        account_id = existing_account_id
        if not account_id:
            accounts = await fw.list_accounts(plaintext)
            if not accounts:
                result.error = "no_accessible_account"
                result.new_status = UpstreamKeyStatus.auto_disabled
                result.disable_reason = "no_accessible_account"
                return result
            account_id = accounts[0].account_id
            result.discovered_account_id = account_id
            result.discovered_account_email = accounts[0].email

        account = await fw.get_account(plaintext, account_id)
        result.suspend_state = account.suspend_state
        result.account_state = account.state

        snap = await fw.list_quotas(plaintext, account_id)
        result.balance_usd = snap.monthly_spend_remaining_usd
        result.monthly_spend_limit_usd = snap.monthly_spend_limit_usd
        result.monthly_spend_used_usd = snap.monthly_spend_used_usd

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
    """在写 session 内根据 result 更新 record。

    防御性：如果 record 已被管理员手动禁用（enabled=False 或 status=disabled），
    保留禁用状态，只更新余额数字与历史 — 避免和管理员操作竞争覆盖。
    """
    # 数据字段总是写（即使探针失败也想记下 attempt）
    if result.ok:
        record.suspend_state = result.suspend_state
        record.account_state = result.account_state
        record.monthly_spend_limit_usd = result.monthly_spend_limit_usd
        record.monthly_spend_used_usd = result.monthly_spend_used_usd
        record.balance_usd = result.balance_usd
        record.balance_updated_at = datetime.now(timezone.utc)
    if result.discovered_account_id and not record.account_id:
        record.account_id = result.discovered_account_id
        record.account_email = result.discovered_account_email

    # status 决策：管理员手动 disabled 时不动
    if not record.enabled or record.status == UpstreamKeyStatus.disabled:
        return
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
    """三段式：① 读快照 → ② 纯 HTTP → ③ 写回。

    HTTP 期间不持有 DB 连接，避免 probe_concurrency 个并发 probe 占满连接池。
    """
    # ① 读快照（短）
    async with session_scope() as session:
        record = await session.get(UpstreamKey, key_id)
        if record is None:
            return None
        plaintext = decrypt_key(record.key_encrypted)
        existing_account_id = record.account_id
        key_preview = record.key_preview  # 用于 ProbeHistory

    # ② HTTP — 不持连接（可能耗时 10-30s）
    result = await _probe_http_only(key_id, plaintext, existing_account_id)

    # ③ 写回（短）— 重新 fetch 防止 stale；写状态、余额、ProbeHistory
    async with session_scope() as session:
        record = await session.get(UpstreamKey, key_id)
        if record is None:
            # 探针期间被删除 — 只返回 HTTP 结果，不写历史
            return result
        _apply_result(record, result)
        session.add(
            ProbeHistory(
                upstream_key_id=record.id,
                upstream_key_preview=record.key_preview or key_preview,
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


# ============================ 轻量级余额刷新 ============================


@dataclass
class BalanceRefreshResult:
    key_id: int
    key_preview: str
    ok: bool
    # 旧余额（更新前）
    previous_balance_usd: float = 0.0
    previous_used_usd: float = 0.0
    # 新余额（更新后）
    balance_usd: float = 0.0
    monthly_spend_limit_usd: float = 0.0
    monthly_spend_used_usd: float = 0.0
    used_percent: float = 0.0
    balance_percent: float = 0.0  # 剩余 / limit
    # 余额变化
    delta_balance_usd: float = 0.0
    delta_used_usd: float = 0.0
    # 状态信息（只读返回，不写库）
    suspend_state: str | None = None
    account_state: str | None = None
    error: str | None = None
    error_type: str | None = None       # key_disabled / unauthorized / no_account / network / unknown
    skipped: bool = False                # True 表示主动跳过（如 Key 被禁用），不算失败
    suggestion: str | None = None       # 给用户的修复建议
    latency_ms: int = 0


def _classify_balance_error(exc: Exception) -> tuple[str, str, str]:
    """把异常映射为 (error_type, friendly_message, suggestion)。"""
    if isinstance(exc, fw.FireworksError):
        if exc.status == 401:
            return (
                "unauthorized",
                "上游 401：Fireworks 拒绝认证，Key 可能已失效或撤销",
                "请到 fireworks.ai 控制台核对，并考虑从池中删除或旋转该 Key",
            )
        if exc.status == 403:
            return (
                "forbidden",
                "上游 403：账户被暂停或权限不足",
                "请检查 Fireworks 账户的 suspendState 是否为 UNSUSPENDED",
            )
        if exc.status == 404:
            return (
                "not_found",
                "上游 404：账户或 quota 资源未找到",
                "可能账户已删除；请到 Fireworks 控制台确认",
            )
        if exc.status == 429:
            return (
                "rate_limited",
                "上游 429：控制面限频；稍后再试",
                "等几秒再点；或全量刷新时降低并发",
            )
        return (
            "upstream_error",
            f"上游 HTTP {exc.status}: {exc}",
            "如持续发生，把 Key 标禁用并联系 Fireworks 支持",
        )
    msg = str(exc).lower()
    if "timeout" in msg or "connecttimeout" in msg or "readtimeout" in msg:
        return (
            "timeout",
            "上游响应超时（可能网络抖动或控制面慢响应）",
            "可以重试；持续超时考虑配 HTTP_PROXY",
        )
    if "connect" in msg or "dns" in msg:
        return (
            "network",
            f"无法连接 Fireworks 控制面: {exc}",
            "检查服务器出网 / DNS / 代理配置",
        )
    return ("unknown", f"{type(exc).__name__}: {exc}", "查看后端日志获取详情")


async def refresh_balance_one(key_id: int) -> BalanceRefreshResult | None:
    """轻量级手动余额刷新：只查 quotas → 写 DB 余额字段。

    与 probe_one 的区别：
    - 不调 get_account（不查 suspend_state，少一次 HTTP）
    - 不触发状态决策（auto_disabled / unhealthy）—— 即使欠费也不禁用
    - 不写 ProbeHistory（不污染探针日志）
    - 手动禁用（status=disabled 或 enabled=False）的 Key **不调用上游**，直接返回 skipped

    三段式（同 probe_one）：① 读快照 + 提前过滤禁用 → ② HTTP → ③ 写回。
    HTTP 期间不持 DB 连接。
    """
    started = time.perf_counter()

    # ① 读快照
    async with session_scope() as session:
        record = await session.get(UpstreamKey, key_id)
        if record is None:
            return None

        prev_balance = record.balance_usd
        prev_used = record.monthly_spend_used_usd
        result = BalanceRefreshResult(
            key_id=record.id,
            key_preview=record.key_preview,
            ok=False,
            previous_balance_usd=prev_balance,
            previous_used_usd=prev_used,
        )

        if not record.enabled or record.status == UpstreamKeyStatus.disabled:
            result.skipped = True
            result.error_type = "key_disabled"
            result.error = "Key 已被禁用，已跳过余额查询"
            result.suggestion = "如要查询，请先在「上游 Key 池」点「启用」"
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            return result

        plaintext = decrypt_key(record.key_encrypted)
        existing_account_id = record.account_id
        snap_suspend_state = record.suspend_state
        snap_account_state = record.account_state

    # ② HTTP — 不持 DB 连接
    discovered_account_id: str | None = None
    discovered_account_email: str | None = None
    snap_data = None
    try:
        if not existing_account_id:
            accounts = await fw.list_accounts(plaintext)
            if not accounts:
                result.error_type = "no_account"
                result.error = "该 Key 没有可访问的 Fireworks 账户"
                result.suggestion = "检查 Key 是否被吊销或拼写错误"
                result.latency_ms = int((time.perf_counter() - started) * 1000)
                return result
            existing_account_id = accounts[0].account_id
            discovered_account_id = existing_account_id
            discovered_account_email = accounts[0].email

        snap_data = await fw.list_quotas(plaintext, existing_account_id)
    except (fw.FireworksError, Exception) as e:
        err_type, friendly, suggest = _classify_balance_error(e)
        result.error_type = err_type
        result.error = friendly
        result.suggestion = suggest
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    # ③ 写回
    async with session_scope() as session:
        record = await session.get(UpstreamKey, key_id)
        if record is None:
            # 期间被删除：只填 result，不写库
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            return result
        if discovered_account_id and not record.account_id:
            record.account_id = discovered_account_id
            record.account_email = discovered_account_email
        record.monthly_spend_limit_usd = snap_data.monthly_spend_limit_usd
        record.monthly_spend_used_usd = snap_data.monthly_spend_used_usd
        record.balance_usd = snap_data.monthly_spend_remaining_usd
        record.balance_updated_at = datetime.now(timezone.utc)

    result.balance_usd = snap_data.monthly_spend_remaining_usd
    result.monthly_spend_limit_usd = snap_data.monthly_spend_limit_usd
    result.monthly_spend_used_usd = snap_data.monthly_spend_used_usd
    if snap_data.monthly_spend_limit_usd > 0:
        result.used_percent = snap_data.monthly_spend_used_usd / snap_data.monthly_spend_limit_usd * 100
        result.balance_percent = result.balance_usd / snap_data.monthly_spend_limit_usd * 100
    result.delta_balance_usd = result.balance_usd - prev_balance
    result.delta_used_usd = snap_data.monthly_spend_used_usd - prev_used
    result.suspend_state = snap_suspend_state
    result.account_state = snap_account_state
    result.ok = True
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result


async def refresh_balance_all() -> dict:
    """并发刷新所有 enabled Key 的余额（限并发 10）。

    返回 {total, ok, fail, skipped, total_balance_usd, ms, items: [...]}。
    - 自动跳过 status=disabled 或 enabled=False 的 Key（refresh_balance_one 内部处理）
    - items 含每把 Key 的余额变化详情，便于 UI 显示
    - 失败摘要按 error_type 聚合（避免 toast 信息过长）
    """
    started = time.perf_counter()
    async with session_scope() as session:
        # 仍选 enabled=True 的（disabled 不浪费时间），覆盖 active/auto_disabled/unhealthy/testing
        # auto_disabled 也包含 — 因为可能是临时禁用，更新余额能帮用户判断是否恢复
        ids = list((await session.execute(
            select(UpstreamKey.id).where(
                UpstreamKey.enabled.is_(True),
                UpstreamKey.status != UpstreamKeyStatus.disabled,
            ).order_by(UpstreamKey.id)
        )).scalars().all())

    if not ids:
        return {"total": 0, "ok": 0, "fail": 0, "skipped": 0,
                "total_balance_usd": 0.0, "ms": 0, "items": [], "error_summary": {}}

    sem = asyncio.Semaphore(10)

    async def _one(kid: int) -> BalanceRefreshResult | None:
        async with sem:
            try:
                return await refresh_balance_one(kid)
            except Exception as e:  # noqa: BLE001
                logger.exception("refresh_balance_one(%s) failed: %s", kid, e)
                return None

    results = await asyncio.gather(*(_one(i) for i in ids))
    ok = sum(1 for r in results if r and r.ok)
    skipped = sum(1 for r in results if r and r.skipped)
    fail = sum(1 for r in results if r is None or (not r.ok and not r.skipped))
    total_balance = sum(r.balance_usd for r in results if r and r.ok)

    # 错误类型聚合：让 UI toast 能显示 "3 把 401 / 2 把超时" 而不是逐条堆叠
    error_summary: dict[str, int] = {}
    for r in results:
        if r is not None and not r.ok and not r.skipped and r.error_type:
            error_summary[r.error_type] = error_summary.get(r.error_type, 0) + 1

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "total": len(ids), "ok": ok, "fail": fail, "skipped": skipped,
        "total_balance_usd": total_balance, "ms": elapsed_ms,
        "error_summary": error_summary,
        "items": [
            {
                "key_id": r.key_id, "key_preview": r.key_preview,
                "ok": r.ok, "skipped": r.skipped,
                "balance_usd": r.balance_usd, "delta_balance_usd": r.delta_balance_usd,
                "used_percent": r.used_percent, "balance_percent": r.balance_percent,
                "error": r.error, "error_type": r.error_type, "suggestion": r.suggestion,
                "latency_ms": r.latency_ms,
            }
            for r in results if r is not None
        ],
    }


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
