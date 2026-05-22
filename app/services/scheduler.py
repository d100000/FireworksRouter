"""上游 Key 调度器：7 种策略。

新增 fill_first：永远取候选池里优先级最高 + ID 最小那把，直到该 Key 进入冷却才让位。
适合按窗口结算的订阅型 Key。
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    KeyModelState,
    KeyModelStateStatus,
    UpstreamKey,
    UpstreamKeyStatus,
)
from app.services import settings as settings_svc


class NoAvailableUpstream(RuntimeError):
    pass


# round_robin 游标：key 是 model_id 字符串
_rr_cursors: dict[str, int] = {}
_RR_CURSOR_LIMIT = 2_147_483_640


async def list_candidates(
    session: AsyncSession,
    exclude_ids: set[int] | None = None,
    requested_model_id: int | None = None,
) -> list[UpstreamKey]:
    """筛选可调度的上游 Key：

    1. enabled=True 且 status=active
    2. 余额 >= 阈值
    3. 整 Key cooldown 未到期
    4. (Key, model) 状态机未挂起（针对 requested_model_id）
    """
    now = datetime.now(timezone.utc)
    min_balance = float(settings_svc.get("probe.min_balance_usd") or 0.0)

    stmt = (
        select(UpstreamKey)
        .where(UpstreamKey.enabled.is_(True))
        .where(UpstreamKey.status == UpstreamKeyStatus.active)
        .where(UpstreamKey.balance_usd >= min_balance)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    # 过滤整 Key 冷却
    rows = [r for r in rows if r.cooldown_until is None or r.cooldown_until <= now]
    if exclude_ids:
        rows = [r for r in rows if r.id not in exclude_ids]
    if not rows or requested_model_id is None:
        return rows

    # 过滤 (Key, model) 冷却
    blocked_pair_stmt = select(KeyModelState.upstream_key_id).where(
        KeyModelState.model_id == requested_model_id,
        KeyModelState.status.in_([KeyModelStateStatus.cooldown, KeyModelStateStatus.blocked]),
        (KeyModelState.cooldown_until.is_(None)) | (KeyModelState.cooldown_until > now),
    )
    blocked_ids = set((await session.execute(blocked_pair_stmt)).scalars().all())
    return [r for r in rows if r.id not in blocked_ids]


def _filter_top_priority(rows: list[UpstreamKey]) -> list[UpstreamKey]:
    if not rows:
        return rows
    top = max(c.priority for c in rows)
    return [c for c in rows if c.priority == top]


def _weighted_pick(rows: list[UpstreamKey]) -> UpstreamKey:
    weights = [max(1, c.weight) for c in rows]
    return random.choices(rows, weights=weights, k=1)[0]


def _round_robin_pick(rows: list[UpstreamKey], model_id: int | None) -> UpstreamKey:
    key = f"m:{model_id or 'any'}"
    rows_sorted = sorted(rows, key=lambda r: r.id)
    cursor = _rr_cursors.get(key, 0)
    chosen = rows_sorted[cursor % len(rows_sorted)]
    new_cursor = (cursor + 1) % _RR_CURSOR_LIMIT
    _rr_cursors[key] = new_cursor
    # 防爆：cursor map 太大就清空
    if len(_rr_cursors) > 4096:
        _rr_cursors.clear()
    return chosen


def _least_used_pick(rows: list[UpstreamKey]) -> UpstreamKey:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return min(rows, key=lambda r: r.last_used_at or epoch)


def _most_balance_pick(rows: list[UpstreamKey]) -> UpstreamKey:
    return max(rows, key=lambda r: r.balance_usd)


def _priority_pick(rows: list[UpstreamKey]) -> UpstreamKey:
    return _weighted_pick(_filter_top_priority(rows))


def _fill_first_pick(rows: list[UpstreamKey]) -> UpstreamKey:
    """顺序填满：先按 priority 降序、再按 id 升序，永远取第一个。

    第一把 Key 进入冷却或不在候选池后，自动让位给次一把。
    """
    top = _filter_top_priority(rows)
    return min(top, key=lambda r: r.id)


def _extract_session_hint(request_body: dict[str, Any] | None, request_headers: dict | None, api_key_id: int | None) -> str | None:
    """按 8 种来源依次找 session 标识。"""
    if request_body:
        # 1. prompt_cache_key
        v = request_body.get("prompt_cache_key")
        if v: return f"pck:{v}"
        # 2. metadata.user_id
        meta = request_body.get("metadata")
        if isinstance(meta, dict) and meta.get("user_id"):
            return f"meta:{meta['user_id']}"
        # 3. conversation_id
        v = request_body.get("conversation_id")
        if v: return f"conv:{v}"
        # 4. user
        v = request_body.get("user")
        if v: return f"user:{v}"
        # 5. messages → FNV-1a 哈希前 N 条
        msgs = request_body.get("messages")
        if isinstance(msgs, list) and msgs:
            digest_input = ""
            for m in msgs[:2]:
                if isinstance(m, dict):
                    digest_input += str(m.get("role", "")) + str(m.get("content", ""))[:500]
            if digest_input:
                return f"msg:{hashlib.sha256(digest_input.encode()).hexdigest()[:16]}"
    if request_headers:
        # 6./7. X-Session-ID / Session-Id
        for h in ("X-Session-ID", "Session-Id", "X-Amp-Thread-Id", "X-Client-Request-Id"):
            v = request_headers.get(h) or request_headers.get(h.lower())
            if v: return f"h:{v}"
    # 8. fallback：api_key_id
    if api_key_id is not None:
        return f"ak:{api_key_id}"
    return None


def _session_sticky_pick(rows: list[UpstreamKey], hint: str | None) -> UpstreamKey:
    if not hint:
        return _weighted_pick(rows)
    rows_sorted = sorted(rows, key=lambda r: r.id)
    digest = hashlib.sha256(hint.encode()).digest()
    idx = int.from_bytes(digest[:8], "big") % len(rows_sorted)
    return rows_sorted[idx]


def supported_strategies() -> list[str]:
    return [
        "weighted_random", "round_robin", "priority",
        "least_used", "most_balance", "session_sticky", "fill_first",
    ]


async def pick(
    session: AsyncSession,
    exclude_ids: set[int] | None = None,
    *,
    request_body: dict[str, Any] | None = None,
    request_headers: dict | None = None,
    api_key_id: int | None = None,
    requested_model_id: int | None = None,
) -> UpstreamKey:
    candidates = await list_candidates(
        session, exclude_ids=exclude_ids, requested_model_id=requested_model_id
    )
    if not candidates:
        raise NoAvailableUpstream("no available upstream keys")

    strategy = settings_svc.get("scheduler.strategy", "weighted_random")

    if strategy == "weighted_random":
        chosen = _weighted_pick(_filter_top_priority(candidates))
    elif strategy == "round_robin":
        chosen = _round_robin_pick(candidates, requested_model_id)
    elif strategy == "priority":
        chosen = _priority_pick(candidates)
    elif strategy == "least_used":
        chosen = _least_used_pick(candidates)
    elif strategy == "most_balance":
        chosen = _most_balance_pick(candidates)
    elif strategy == "fill_first":
        chosen = _fill_first_pick(candidates)
    elif strategy == "session_sticky":
        hint = _extract_session_hint(request_body, request_headers, api_key_id)
        chosen = _session_sticky_pick(candidates, hint)
    else:
        chosen = _weighted_pick(_filter_top_priority(candidates))

    chosen.last_used_at = datetime.now(timezone.utc)
    chosen.total_requests += 1
    return chosen
