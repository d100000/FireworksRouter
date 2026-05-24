"""日志管理 API：列表、批量删除、清理状态、立即清理。

涵盖 3 类持久化日志：
  - system_logs        应用日志（loguru 入库）
  - request_logs       OpenAI 调用流水
  - probe_history      余额探针历史
  - key_metric_buckets 5 分钟监控桶

只有列表/删除入口对外暴露；保留期 + 清理走 settings。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.api.deps import SessionDep, require_admin
from app.models import (
    KeyMetricBucket,
    ProbeHistory,
    RequestLog,
    SystemLog,
)
from app.services import metrics as metrics_svc

router = APIRouter(
    prefix="/admin",
    tags=["admin/logs"],
    dependencies=[Depends(require_admin)],
)


# --------------------------------------------------------------------------- #
# system_logs（应用日志）
# --------------------------------------------------------------------------- #


def _serialize_system_log(row: SystemLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "level": row.level,
        "module": row.module,
        "function": row.function,
        "line": row.line,
        "message": row.message,
        "request_id": row.request_id,
        "extra": row.extra,  # 客户端自己 JSON.parse
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/system-logs")
async def list_system_logs(
    session: SessionDep,
    level: str | None = Query(None, description="DEBUG/INFO/WARNING/ERROR/CRITICAL（大写）"),
    module: str | None = Query(None, description="模糊匹配 module，如 `app.services`"),
    search: str | None = Query(None, description="模糊匹配 message"),
    request_id: str | None = Query(None),
    start: datetime | None = Query(None, description="timestamp >= start"),
    end: datetime | None = Query(None, description="timestamp <= end"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    stmt = select(SystemLog)
    count_stmt = select(func.count(SystemLog.id))
    if level:
        stmt = stmt.where(SystemLog.level == level.upper())
        count_stmt = count_stmt.where(SystemLog.level == level.upper())
    if module:
        stmt = stmt.where(SystemLog.module.like(f"%{module}%"))
        count_stmt = count_stmt.where(SystemLog.module.like(f"%{module}%"))
    if search:
        stmt = stmt.where(SystemLog.message.like(f"%{search}%"))
        count_stmt = count_stmt.where(SystemLog.message.like(f"%{search}%"))
    if request_id:
        stmt = stmt.where(SystemLog.request_id == request_id)
        count_stmt = count_stmt.where(SystemLog.request_id == request_id)
    if start:
        stmt = stmt.where(SystemLog.timestamp >= start)
        count_stmt = count_stmt.where(SystemLog.timestamp >= start)
    if end:
        stmt = stmt.where(SystemLog.timestamp <= end)
        count_stmt = count_stmt.where(SystemLog.timestamp <= end)

    total = int((await session.execute(count_stmt)).scalar() or 0)
    stmt = stmt.order_by(SystemLog.timestamp.desc(), SystemLog.id.desc()).limit(limit).offset(offset)
    rows = list((await session.execute(stmt)).scalars().all())

    return {
        "total": total,
        "items": [_serialize_system_log(r) for r in rows],
    }


class DeleteFilter(BaseModel):
    """通用删除筛选：传啥都按 AND 拼，不传就匹配所有（危险，要二次确认）。"""

    ids: list[int] | None = None
    level: str | None = None
    module: str | None = None
    search: str | None = None
    request_id: str | None = None
    before: datetime | None = Field(None, description="timestamp < before 才删")
    after: datetime | None = None
    confirm_all: bool = Field(False, description="不带任何筛选时必须设为 True 才允许全表删")


@router.post("/system-logs/bulk-delete")
async def delete_system_logs(
    session: SessionDep,
    filt: DeleteFilter,
) -> dict[str, int]:
    stmt = delete(SystemLog)
    has_filter = False
    if filt.ids:
        stmt = stmt.where(SystemLog.id.in_(filt.ids))
        has_filter = True
    if filt.level:
        stmt = stmt.where(SystemLog.level == filt.level.upper())
        has_filter = True
    if filt.module:
        stmt = stmt.where(SystemLog.module.like(f"%{filt.module}%"))
        has_filter = True
    if filt.search:
        stmt = stmt.where(SystemLog.message.like(f"%{filt.search}%"))
        has_filter = True
    if filt.request_id:
        stmt = stmt.where(SystemLog.request_id == filt.request_id)
        has_filter = True
    if filt.before:
        stmt = stmt.where(SystemLog.timestamp < filt.before)
        has_filter = True
    if filt.after:
        stmt = stmt.where(SystemLog.timestamp > filt.after)
        has_filter = True
    if not has_filter and not filt.confirm_all:
        return {"deleted": 0, "error": "no filter; pass confirm_all=true to wipe entire table"}

    result = await session.execute(stmt)
    return {"deleted": result.rowcount or 0}


# --------------------------------------------------------------------------- #
# request_logs（调用流水）— 列表已有 /admin/request-logs；这里只加批量删
# --------------------------------------------------------------------------- #


class RequestLogDeleteFilter(BaseModel):
    ids: list[int] | None = None
    status_code_gte: int | None = Field(None, description="只删 status_code >= 此值的（如 400 删错误）")
    before: datetime | None = None
    after: datetime | None = None
    api_key_id: int | None = None
    upstream_key_id: int | None = None
    confirm_all: bool = False


@router.post("/request-logs/bulk-delete")
async def delete_request_logs(
    session: SessionDep,
    filt: RequestLogDeleteFilter,
) -> dict[str, int]:
    stmt = delete(RequestLog)
    has_filter = False
    if filt.ids:
        stmt = stmt.where(RequestLog.id.in_(filt.ids))
        has_filter = True
    if filt.status_code_gte is not None:
        stmt = stmt.where(RequestLog.status_code >= filt.status_code_gte)
        has_filter = True
    if filt.before:
        stmt = stmt.where(RequestLog.created_at < filt.before)
        has_filter = True
    if filt.after:
        stmt = stmt.where(RequestLog.created_at > filt.after)
        has_filter = True
    if filt.api_key_id is not None:
        stmt = stmt.where(RequestLog.api_key_id == filt.api_key_id)
        has_filter = True
    if filt.upstream_key_id is not None:
        stmt = stmt.where(RequestLog.upstream_key_id == filt.upstream_key_id)
        has_filter = True
    if not has_filter and not filt.confirm_all:
        return {"deleted": 0, "error": "no filter; pass confirm_all=true to wipe entire table"}

    result = await session.execute(stmt)
    return {"deleted": result.rowcount or 0}


# --------------------------------------------------------------------------- #
# probe_history（探针）
# --------------------------------------------------------------------------- #


class ProbeDeleteFilter(BaseModel):
    ids: list[int] | None = None
    before: datetime | None = None
    upstream_key_id: int | None = None
    confirm_all: bool = False


@router.post("/probe-history/bulk-delete")
async def delete_probe_history(
    session: SessionDep,
    filt: ProbeDeleteFilter,
) -> dict[str, int]:
    stmt = delete(ProbeHistory)
    has_filter = False
    if filt.ids:
        stmt = stmt.where(ProbeHistory.id.in_(filt.ids))
        has_filter = True
    if filt.before:
        stmt = stmt.where(ProbeHistory.created_at < filt.before)
        has_filter = True
    if filt.upstream_key_id is not None:
        stmt = stmt.where(ProbeHistory.upstream_key_id == filt.upstream_key_id)
        has_filter = True
    if not has_filter and not filt.confirm_all:
        return {"deleted": 0, "error": "no filter; pass confirm_all=true to wipe entire table"}

    result = await session.execute(stmt)
    return {"deleted": result.rowcount or 0}


# --------------------------------------------------------------------------- #
# 总览：表大小 + 当前保留期配置 + 立即清理
# --------------------------------------------------------------------------- #


@router.get("/cleanup/status")
async def cleanup_status(session: SessionDep) -> dict[str, Any]:
    """各表行数 + 最早/最新时间，配合 settings 显示保留策略让用户决策。"""
    from app.services import settings as settings_svc

    async def _table_stats(model, ts_col):
        cnt = int((await session.execute(select(func.count(model.id)))).scalar() or 0)
        oldest = (await session.execute(select(func.min(ts_col)))).scalar()
        newest = (await session.execute(select(func.max(ts_col)))).scalar()
        return {
            "rows": cnt,
            "oldest": oldest.isoformat() if oldest else None,
            "newest": newest.isoformat() if newest else None,
        }

    tables = {
        "system_logs": await _table_stats(SystemLog, SystemLog.timestamp),
        "request_logs": await _table_stats(RequestLog, RequestLog.created_at),
        "probe_history": await _table_stats(ProbeHistory, ProbeHistory.created_at),
        "key_metric_buckets": await _table_stats(KeyMetricBucket, KeyMetricBucket.bucket_start),
    }

    retention = {
        "logs_retention_days": int(settings_svc.get("logs_retention_days") or 30),
        "system_logs_retention_days": int(settings_svc.get("system_logs_retention_days") or 14),
        "probe_history_retention_days": int(settings_svc.get("probe_history_retention_days") or 7),
        "metric_buckets_retention_hours": int(settings_svc.get("metric_buckets_retention_hours") or 25),
        "system_log_min_level": str(settings_svc.get("system_log_min_level") or "WARNING").upper(),
    }

    return {"tables": tables, "retention": retention}


@router.post("/cleanup/run-now")
async def cleanup_run_now() -> dict[str, Any]:
    """手动触发一次清理（按当前 settings 保留期），返回每张表删了多少行。"""
    deleted = await metrics_svc.run_cleanup_once()
    return {"deleted": deleted, "ran_at": datetime.now(timezone.utc).isoformat()}


# --------------------------------------------------------------------------- #
# schema 消毒统计：用 system_logs 的 category=schema_sanitize 标签
# --------------------------------------------------------------------------- #


@router.get("/stats/schema-sanitize")
async def schema_sanitize_stats(
    session: SessionDep,
    hours: int = Query(24, ge=1, le=24 * 30, description="过去 N 小时"),
) -> dict[str, Any]:
    """统计过去 N 小时内 gateway 帮客户端修复了多少 schema 问题。

    数据来源：system_logs 表里 extra 含 `"category":"schema_sanitize"` 的事件。
    每个事件代表一次请求触发了消毒（一个请求里可能修了多个问题，extra.categories
    里有细分计数）。

    返回：
      total_events      触发了消毒的请求数
      total_items       被消毒的具体问题数（聚合所有事件的 categories）
      by_category       按类型分组的计数
                         {lookaround, atomic_group, comment, backref,
                          possessive_quantifier, ref_inline, ref_circular,
                          ref_dangling, pattern_properties_key, ...}
      by_source         按来源分组（anthropic_tools / openai）
      recent            最近 10 条事件预览（含 message + timestamp + categories）
    """
    import json as _json
    from app.models import SystemLog
    from sqlalchemy import select

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    # extra 是 TEXT，没法在 DB 端 JSON 解析（要兼容 SQLite + PG）— LIKE 预过滤
    # 然后 Python 端聚合。消毒事件量本身不大（每次有问题请求 1 条 WARNING）。
    stmt = (
        select(SystemLog)
        .where(SystemLog.timestamp >= cutoff)
        .where(SystemLog.extra.like('%"category":%"schema_sanitize"%'))
        .order_by(SystemLog.timestamp.desc())
    )
    rows = list((await session.execute(stmt)).scalars().all())

    by_category: dict[str, int] = {}
    by_source: dict[str, int] = {}
    total_items = 0
    recent: list[dict[str, Any]] = []

    for r in rows:
        extra_dict: dict[str, Any] = {}
        if r.extra:
            try:
                extra_dict = _json.loads(r.extra)
            except (_json.JSONDecodeError, ValueError):
                continue
        if extra_dict.get("category") != "schema_sanitize":
            continue
        cats = extra_dict.get("categories") or {}
        if isinstance(cats, dict):
            for cat, cnt in cats.items():
                try:
                    n = int(cnt)
                except (TypeError, ValueError):
                    n = 0
                by_category[cat] = by_category.get(cat, 0) + n
                total_items += n
        src = extra_dict.get("source") or "unknown"
        by_source[src] = by_source.get(src, 0) + 1
        if len(recent) < 10:
            recent.append({
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "source": src,
                "categories": cats if isinstance(cats, dict) else {},
                "message": (r.message or "")[:300],
            })

    return {
        "hours": hours,
        "total_events": len(rows),
        "total_items": total_items,
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "by_source": by_source,
        "recent": recent,
    }
