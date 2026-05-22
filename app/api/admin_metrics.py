"""Per-Key 监控 API：5 分钟桶时序、错误分布、健康汇总。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select

from app.api.deps import SessionDep, require_admin
from app.models import (
    KeyMetricBucket,
    KeyModelState,
    RequestLog,
    UpstreamKey,
    UpstreamKeyStatus,
)

router = APIRouter(
    prefix="/admin",
    tags=["admin/metrics"],
    dependencies=[Depends(require_admin)],
)


@router.get("/upstream-keys/{key_id}/recent-requests")
async def key_recent_requests(
    key_id: int,
    session: SessionDep,
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """该 Key 最近 N 条调用明细（含 endpoint / api_key 名称首字 / 流式 / 耗时）。"""
    stmt = (
        select(RequestLog)
        .where(RequestLog.upstream_key_id == key_id)
        .order_by(desc(RequestLog.id))
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return {
        "key_id": key_id,
        "items": [
            {
                "id": r.id, "created_at": r.created_at,
                "api_key_label": r.api_key_label,
                "api_key_preview": r.api_key_preview,
                "endpoint": r.endpoint,
                "public_model": r.public_model,
                "stream": r.stream,
                "status_code": r.status_code,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "latency_ms": r.latency_ms,
                "ttft_ms": r.ttft_ms,
                "billed_cost_usd": r.billed_cost_usd,
            }
            for r in rows
        ],
    }


@router.get("/upstream-keys/{key_id}/metrics")
async def key_metrics(
    key_id: int,
    session: SessionDep,
    hours: int = Query(24, ge=1, le=168),
) -> dict[str, Any]:
    """取该 Key 最近 N 小时的 5 分钟桶时序数据。"""
    key = await session.get(UpstreamKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="not found")
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = (
        select(KeyMetricBucket)
        .where(KeyMetricBucket.upstream_key_id == key_id)
        .where(KeyMetricBucket.bucket_start >= since)
        .order_by(KeyMetricBucket.bucket_start)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return {
        "key_id": key_id,
        "key_preview": key.key_preview,
        "hours": hours,
        "since": since.isoformat(),
        "series": [
            {
                "ts": b.bucket_start.isoformat(),
                "success": b.success,
                "failed": b.failed,
                "prompt_tokens": b.prompt_tokens,
                "completion_tokens": b.completion_tokens,
                "cost_usd": b.cost_usd,
                "avg_latency_ms": b.avg_latency_ms,
                "max_latency_ms": b.max_latency_ms,
                "min_latency_ms": b.min_latency_ms,
            }
            for b in rows
        ],
    }


@router.get("/upstream-keys/{key_id}/error-breakdown")
async def key_error_breakdown(
    key_id: int,
    session: SessionDep,
    hours: int = Query(24, ge=1, le=168),
) -> dict[str, Any]:
    """按 HTTP 状态码分组该 Key 的错误分布。"""
    key = await session.get(UpstreamKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="not found")
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = (
        select(RequestLog.status_code, func.count(RequestLog.id))
        .where(RequestLog.upstream_key_id == key_id, RequestLog.created_at >= since)
        .group_by(RequestLog.status_code)
        .order_by(desc(func.count(RequestLog.id)))
    )
    rows = list((await session.execute(stmt)).all())
    return {
        "key_id": key_id,
        "hours": hours,
        "items": [{"status_code": int(r[0] or 0), "count": int(r[1])} for r in rows],
    }


@router.get("/upstream-keys/{key_id}/model-states")
async def key_model_states_view(
    key_id: int,
    session: SessionDep,
) -> dict[str, Any]:
    """看该 Key 各模型的状态机（哪些 model 被挂起多久）。"""
    stmt = select(KeyModelState).where(KeyModelState.upstream_key_id == key_id)
    rows = list((await session.execute(stmt)).scalars().all())
    return {
        "key_id": key_id,
        "items": [
            {
                "model_id": s.model_id,
                "status": s.status.value,
                "cooldown_until": s.cooldown_until,
                "last_error_code": s.last_error_code,
                "last_error_message": s.last_error_message,
                "last_error_at": s.last_error_at,
                "backoff_level": s.backoff_level,
                "next_retry_after": s.next_retry_after,
            }
            for s in rows
        ],
    }


@router.get("/stats/keys-health")
async def keys_health(session: SessionDep) -> dict[str, Any]:
    """所有 Key 的健康度汇总（给 Dashboard 用）。"""
    now = datetime.now(timezone.utc)
    keys = list((await session.execute(select(UpstreamKey).order_by(UpstreamKey.id))).scalars().all())

    items = []
    cooldown_count = 0
    for k in keys:
        in_cool = bool(k.cooldown_until and k.cooldown_until > now)
        if in_cool:
            cooldown_count += 1
        items.append({
            "id": k.id,
            "name": k.name,
            "key_preview": k.key_preview,
            "status": k.status.value,
            "enabled": k.enabled,
            "in_cooldown": in_cool,
            "cooldown_until": k.cooldown_until,
            "cooldown_reason": k.cooldown_reason,
            "backoff_level": k.backoff_level,
            "balance_usd": k.balance_usd,
            "success_count_24h": k.success_count_24h,
            "failed_count_24h": k.failed_count_24h,
            "stability_score": k.stability_score,
            "last_success_at": k.last_success_at,
            "last_failed_at": k.last_failed_at,
            "last_error_message": k.last_error_message,
        })

    # Top 排行
    by_score = sorted(items, key=lambda x: x["stability_score"], reverse=True)
    return {
        "total": len(keys),
        "active": sum(1 for k in keys if k.status == UpstreamKeyStatus.active and k.enabled),
        "in_cooldown": cooldown_count,
        "auto_disabled": sum(1 for k in keys if k.status == UpstreamKeyStatus.auto_disabled),
        "top_stable": by_score[:5],
        "bottom_stable": [x for x in by_score[-5:] if x["failed_count_24h"] > 0][::-1],
        "items": items,
    }
