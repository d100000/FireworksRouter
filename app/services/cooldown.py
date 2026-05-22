"""按 HTTP 状态码差异化的退避决策器。

参考 CLIProxyAPI 的 conductor 错误处理矩阵，区分：
- 401/403：凭据失效 → 整 Key 冷却 30 min
- 402：余额不足 → 整 Key 冷却 1 h
- 404：模型不支持 → 仅 (Key, model) 冷却 12 h
- 408/5xx：瞬时故障 → 整 Key 1 min，指数退避
- 429：限频 → (Key, model) 1s 起步指数到 30 min
- 400/422：客户端错误 → 不冷却、不重试，透传
- timeout：30s 整 Key
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import (
    KeyModelState,
    KeyModelStateStatus,
    UpstreamKey,
    UpstreamKeyStatus,
)
from app.utils.logger import logger


@dataclass
class Decision:
    retryable: bool
    cool_whole_key: bool
    cool_key_model: bool
    cooldown_seconds: int
    reason: str
    error_code: int


# 错误码决策表：(retryable, cool_whole_key, cool_key_model, initial_s, max_s, reason)
_DECISION_TABLE: dict[int, tuple[bool, bool, bool, int, int, str]] = {
    0:   (True,  True,  False, 30,   300,    "network_or_timeout"),
    400: (False, False, False, 0,    0,      "client_error_bad_request"),
    401: (False, True,  False, 1800, 1800,   "auth_failed"),
    402: (False, True,  False, 3600, 3600,   "payment_required"),
    403: (False, True,  False, 1800, 1800,   "forbidden"),
    404: (False, False, True,  43200, 43200, "model_not_supported"),
    408: (True,  True,  False, 60,   1800,   "request_timeout"),
    422: (False, False, False, 0,    0,      "client_error_unprocessable"),
    429: (True,  False, True,  1,    1800,   "rate_limited"),
    500: (True,  True,  False, 60,   1800,   "upstream_5xx"),
    502: (True,  True,  False, 60,   1800,   "upstream_5xx"),
    503: (True,  True,  False, 60,   1800,   "upstream_5xx"),
    504: (True,  True,  False, 60,   1800,   "upstream_5xx"),
}


def _lookup(http_status: int) -> tuple[bool, bool, bool, int, int, str]:
    if http_status in _DECISION_TABLE:
        return _DECISION_TABLE[http_status]
    if 500 <= http_status < 600:
        return _DECISION_TABLE[500]
    if 400 <= http_status < 500:
        return _DECISION_TABLE[400]
    return _DECISION_TABLE[0]


def _backoff_seconds(initial: int, level: int, max_s: int) -> int:
    if initial <= 0:
        return 0
    return min(int(initial * math.pow(2, level)), max_s)


async def apply_success(upstream_key_id: int, model_id: int | None) -> None:
    """请求成功：清除冷却，重置 backoff_level。"""
    async with session_scope() as session:
        key = await session.get(UpstreamKey, upstream_key_id)
        if key is not None:
            key.cooldown_until = None
            key.cooldown_reason = None
            key.backoff_level = 0
            key.last_success_at = datetime.now(timezone.utc)
            if key.status == UpstreamKeyStatus.unhealthy:
                # 健康度恢复
                key.status = UpstreamKeyStatus.active
        if model_id is not None:
            stmt = select(KeyModelState).where(
                KeyModelState.upstream_key_id == upstream_key_id,
                KeyModelState.model_id == model_id,
            )
            state = (await session.execute(stmt)).scalar_one_or_none()
            if state is not None:
                state.status = KeyModelStateStatus.ready
                state.cooldown_until = None
                state.next_retry_after = None
                state.backoff_level = 0


async def apply_error(
    upstream_key_id: int,
    model_id: int | None,
    *,
    http_status: int,
    error_message: str | None = None,
) -> Decision:
    """请求失败：按错误码决策 + 写入冷却态。"""
    retryable, cool_whole, cool_pair, initial_s, max_s, reason = _lookup(http_status)
    decision = Decision(
        retryable=retryable, cool_whole_key=cool_whole, cool_key_model=cool_pair,
        cooldown_seconds=0, reason=reason, error_code=http_status,
    )

    if not (cool_whole or cool_pair):
        # 客户端错误 422/400：不写库，直接返回
        return decision

    now = datetime.now(timezone.utc)

    async with session_scope() as session:
        if cool_whole:
            key = await session.get(UpstreamKey, upstream_key_id)
            if key is not None:
                lvl = key.backoff_level if retryable else 0
                seconds = _backoff_seconds(initial_s, lvl, max_s)
                key.cooldown_until = now + timedelta(seconds=seconds)
                key.cooldown_reason = f"{reason}_http_{http_status}"
                key.backoff_level = lvl + 1 if retryable else 0
                key.last_failed_at = now
                if error_message:
                    key.last_error_message = error_message[:500]
                # 长冷却的非可重试错误（401/402/403）置 auto_disabled
                if not retryable and seconds >= 1800:
                    key.status = UpstreamKeyStatus.auto_disabled
                    key.auto_disable_reason = f"http_{http_status}_{reason}"
                    if key.disabled_at is None:
                        key.disabled_at = now
                decision.cooldown_seconds = seconds

        if cool_pair and model_id is not None:
            stmt = select(KeyModelState).where(
                KeyModelState.upstream_key_id == upstream_key_id,
                KeyModelState.model_id == model_id,
            )
            state = (await session.execute(stmt)).scalar_one_or_none()
            if state is None:
                state = KeyModelState(
                    upstream_key_id=upstream_key_id, model_id=model_id,
                    backoff_level=0,
                )
                session.add(state)
                await session.flush()
            lvl = state.backoff_level if retryable else 0
            seconds = _backoff_seconds(initial_s, lvl, max_s)
            state.status = KeyModelStateStatus.cooldown if retryable else KeyModelStateStatus.blocked
            state.cooldown_until = now + timedelta(seconds=seconds)
            state.next_retry_after = state.cooldown_until
            state.last_error_code = http_status
            if error_message:
                state.last_error_message = error_message[:500]
            state.last_error_at = now
            state.backoff_level = lvl + 1 if retryable else 0
            # 冲突时 cool_whole 已经设置了，取较大值
            if decision.cooldown_seconds == 0:
                decision.cooldown_seconds = seconds

    logger.info(
        "cooldown.apply_error key={} model={} http={} → {} ({}s)",
        upstream_key_id, model_id, http_status, reason, decision.cooldown_seconds,
    )
    return decision
