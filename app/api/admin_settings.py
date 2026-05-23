"""系统运行时设置：DB KV 表读写。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import require_admin
from app.services import scheduler, settings as settings_svc

router = APIRouter(
    prefix="/admin",
    tags=["admin/settings"],
    dependencies=[Depends(require_admin)],
)


class SettingsOut(BaseModel):
    items: dict[str, Any]
    schedule_strategies: list[str]


class SettingsUpdate(BaseModel):
    items: dict[str, Any]


_ALLOWED_KEYS = {
    "scheduler.strategy",
    "scheduler.session_sticky_field",
    "gateway.max_retry",
    "gateway.max_retry_credentials",
    "gateway.max_retry_interval_s",
    "probe.min_balance_usd",
    "probe.interval_minutes",
    "cooldown.401_seconds",
    "cooldown.402_seconds",
    "cooldown.404_seconds",
    "cooldown.429_initial_seconds",
    "cooldown.429_max_seconds",
    "cooldown.5xx_initial_seconds",
    "cooldown.5xx_max_seconds",
    "logs_retention_days",
    "system_logs_retention_days",
    "probe_history_retention_days",
    "metric_buckets_retention_hours",
    "system_log_min_level",
}

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@router.get("/settings", response_model=SettingsOut)
async def get_settings() -> SettingsOut:
    items = await settings_svc.get_all()
    return SettingsOut(items=items, schedule_strategies=scheduler.supported_strategies())


@router.patch("/settings", response_model=SettingsOut)
async def update_settings(payload: SettingsUpdate) -> SettingsOut:
    for k, v in payload.items.items():
        if k not in _ALLOWED_KEYS:
            raise HTTPException(status_code=400, detail=f"unknown key: {k}")
        if k == "scheduler.strategy" and v not in scheduler.supported_strategies():
            raise HTTPException(status_code=400, detail=f"unknown strategy: {v}")
        if k == "system_log_min_level":
            up = str(v).upper()
            if up not in _VALID_LOG_LEVELS:
                raise HTTPException(status_code=400, detail=f"invalid log level: {v}")
            v = up
        await settings_svc.set_value(k, v)
    # 改 log min level 后立刻刷新 DB sink 过滤阈值，不用重启
    if "system_log_min_level" in payload.items:
        from app.utils.logger import refresh_min_level
        refresh_min_level()
    items = await settings_svc.get_all()
    return SettingsOut(items=items, schedule_strategies=scheduler.supported_strategies())
