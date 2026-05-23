"""运行时系统设置：DB KV + 内存缓存 + 优先级（DB → env 默认）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import SystemSetting

# 默认值（DB 中没有时使用）
DEFAULTS: dict[str, Any] = {
    "scheduler.strategy": "weighted_random",
    "scheduler.session_sticky_field": "prompt_cache_key",
    "gateway.max_retry": 3,
    "gateway.max_retry_credentials": 3,
    "gateway.max_retry_interval_s": 30,
    "probe.min_balance_usd": 0.5,
    "probe.interval_minutes": 15,
    "cooldown.401_seconds": 1800,
    "cooldown.402_seconds": 3600,
    "cooldown.404_seconds": 43200,
    "cooldown.429_initial_seconds": 1,
    "cooldown.429_max_seconds": 1800,
    "cooldown.5xx_initial_seconds": 60,
    "cooldown.5xx_max_seconds": 1800,
    # 日志保留期（天）
    "logs_retention_days": 30,            # request_logs（API 调用流水）
    "system_logs_retention_days": 14,     # system_logs（应用日志：错误/异常等）
    "probe_history_retention_days": 7,    # probe_history（余额探针历史）
    "metric_buckets_retention_hours": 25, # key_metric_buckets（5 分钟桶）
    # 应用日志入库最低级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）
    # 默认 WARNING+ — INFO/DEBUG 量大不入 DB，避免膨胀
    "system_log_min_level": "WARNING",
}

_cache: dict[str, Any] = {}


async def load_all() -> dict[str, Any]:
    """从 DB 加载所有设置并填充内存缓存。"""
    async with session_scope() as session:
        rows = list((await session.execute(select(SystemSetting))).scalars().all())
    for r in rows:
        _cache[r.key] = r.value
    return dict(_cache)


def get(key: str, default: Any = None) -> Any:
    if key in _cache:
        return _cache[key]
    if key in DEFAULTS:
        return DEFAULTS[key]
    return default


async def set_value(key: str, value: Any, description: str | None = None) -> None:
    async with session_scope() as session:
        record = (await session.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()
        if record is None:
            record = SystemSetting(key=key, value=value, description=description)
            session.add(record)
        else:
            record.value = value
            if description is not None:
                record.description = description
    _cache[key] = value


async def get_all() -> dict[str, Any]:
    out = dict(DEFAULTS)
    out.update(_cache)
    return out
