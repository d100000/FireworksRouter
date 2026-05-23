from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, require_admin
from app.crypto import decrypt_key, encrypt_key
from app.models import (
    ApiKey,
    ApiKeyStatus,
    ProbeHistory,
    RequestLog,
    UpstreamKey,
    UpstreamKeyStatus,
)
from app.services import balance as balance_svc
from app.services import upstream as upstream_svc
from app.utils.tokens import generate_user_token, hash_token

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


# ============================= Upstream Keys =============================


class UpstreamKeyOut(BaseModel):
    id: int
    name: str
    key_preview: str
    account_id: str | None
    account_email: str | None
    status: str
    suspend_state: str | None
    account_state: str | None
    enabled: bool
    priority: int
    weight: int
    balance_usd: float
    monthly_spend_limit_usd: float
    monthly_spend_used_usd: float
    balance_percent: float = 0.0
    balance_updated_at: datetime | None
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    consecutive_failures: int
    auto_disable_reason: str | None
    last_used_at: datetime | None
    last_success_at: datetime | None
    last_failed_at: datetime | None
    last_error_message: str | None
    cooldown_until: datetime | None
    cooldown_reason: str | None
    backoff_level: int
    success_count_24h: int
    failed_count_24h: int
    stability_score: float
    # 物化字段（避免列表页 N+1 查询）
    recent_buckets: list[dict] | None = None
    recent_buckets_updated_at: datetime | None = None
    last_probe_ok: bool | None = None
    last_probe_ms: int = 0
    last_probe_at: datetime | None = None
    notes: str | None
    created_at: datetime

    @classmethod
    def from_orm(cls, k: UpstreamKey) -> "UpstreamKeyOut":
        pct = 0.0
        if k.monthly_spend_limit_usd > 0:
            pct = max(0.0, min(100.0, (k.balance_usd / k.monthly_spend_limit_usd) * 100.0))
        return cls(
            id=k.id, name=k.name, key_preview=k.key_preview,
            account_id=k.account_id, account_email=k.account_email,
            status=k.status.value, suspend_state=k.suspend_state, account_state=k.account_state,
            enabled=k.enabled, priority=k.priority, weight=k.weight,
            balance_usd=k.balance_usd,
            monthly_spend_limit_usd=k.monthly_spend_limit_usd,
            monthly_spend_used_usd=k.monthly_spend_used_usd,
            balance_percent=pct,
            balance_updated_at=k.balance_updated_at,
            total_requests=k.total_requests,
            total_input_tokens=k.total_input_tokens, total_output_tokens=k.total_output_tokens,
            consecutive_failures=k.consecutive_failures, auto_disable_reason=k.auto_disable_reason,
            last_used_at=k.last_used_at,
            last_success_at=k.last_success_at, last_failed_at=k.last_failed_at,
            last_error_message=k.last_error_message,
            cooldown_until=k.cooldown_until, cooldown_reason=k.cooldown_reason,
            backoff_level=k.backoff_level,
            success_count_24h=k.success_count_24h, failed_count_24h=k.failed_count_24h,
            stability_score=k.stability_score,
            recent_buckets=k.recent_buckets_json,
            recent_buckets_updated_at=k.recent_buckets_updated_at,
            last_probe_ok=k.last_probe_ok,
            last_probe_ms=k.last_probe_ms,
            last_probe_at=k.last_probe_at,
            notes=k.notes, created_at=k.created_at,
        )


class UpstreamKeyCreate(BaseModel):
    key: str = Field(min_length=10)
    name: str | None = None
    notes: str | None = None
    priority: int = 0
    weight: int = 100


class UpstreamKeyUpdate(BaseModel):
    name: str | None = None
    notes: str | None = None
    priority: int | None = None
    weight: int | None = None
    enabled: bool | None = None
    rpm_limit: int | None = None


class UpstreamKeyBatchCreate(BaseModel):
    keys: str = Field(description="多行：每行一个 fw_xxx，可附 ',name' 后缀")


class UpstreamKeyBatchResultItem(BaseModel):
    key_preview: str
    created: bool
    status: str | None = None
    note: str | None = None
    error: str | None = None


class UpstreamKeyBatchResult(BaseModel):
    total: int
    created: int
    duplicated: int
    failed: int
    items: list[UpstreamKeyBatchResultItem]


@router.get("/upstream-keys", response_model=list[UpstreamKeyOut])
async def list_upstream_keys(session: SessionDep):
    rows = await upstream_svc.list_all(session)
    return [UpstreamKeyOut.from_orm(r) for r in rows]


@router.post("/upstream-keys", response_model=UpstreamKeyOut, status_code=status.HTTP_201_CREATED)
async def create_upstream_key(payload: UpstreamKeyCreate, session: SessionDep):
    try:
        result = await upstream_svc.register_key(
            session, payload.key, name=payload.name, notes=payload.notes,
            priority=payload.priority, weight=payload.weight,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"message": str(e), "type": "invalid_request_error"}},
        ) from None
    return UpstreamKeyOut.from_orm(result.key)


@router.post("/upstream-keys/batch", response_model=UpstreamKeyBatchResult)
async def batch_create_upstream_keys(payload: UpstreamKeyBatchCreate, session: SessionDep):
    """批量导入：快速入库（< 100ms）+ 后台异步并发探针（不阻塞响应）。

    设计：
    1. 同步遍历，每把 Key 只做加密 + 去重 + DB 写入（register_key_quick），不调上游
    2. 收集所有新创建 Key 的 ID
    3. 用 asyncio.create_task 派发后台任务，Semaphore(10) 限制并发探针
    4. 立即返回结果（status=testing），前端列表 5s 自动刷新看 testing → active 切换
    """
    import asyncio

    items: list[UpstreamKeyBatchResultItem] = []
    created = 0
    duplicated = 0
    failed = 0
    new_key_ids: list[int] = []

    lines = [ln.strip() for ln in payload.keys.splitlines() if ln.strip()]
    for ln in lines:
        parts = ln.split(",", 1)
        key = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else None
        try:
            r = await upstream_svc.register_key_quick(session, key, name=name)
        except ValueError as e:
            items.append(UpstreamKeyBatchResultItem(key_preview=key[:10], created=False, error=str(e)))
            failed += 1
            continue

        if not r.created and r.note == "duplicate":
            duplicated += 1
            items.append(UpstreamKeyBatchResultItem(
                key_preview=r.key.key_preview, created=False,
                status=r.key.status.value, note="duplicate"))
        else:
            created += 1
            items.append(UpstreamKeyBatchResultItem(
                key_preview=r.key.key_preview, created=True,
                status=r.key.status.value, note="探针中…"))
            new_key_ids.append(r.key.id)

    # commit 让后台任务能查到记录
    await session.commit()

    # 派发后台并发探针（限制 10 并发，避免 Fireworks 控制面压力）
    if new_key_ids:
        sem = asyncio.Semaphore(10)

        async def _probe_one(kid: int) -> None:
            async with sem:
                await upstream_svc.probe_after_register(kid)

        # 创建任务但不 await — 让 batch 端点立即返回
        for kid in new_key_ids:
            asyncio.create_task(_probe_one(kid))

    return UpstreamKeyBatchResult(
        total=len(lines), created=created, duplicated=duplicated, failed=failed, items=items
    )


@router.get("/upstream-keys/{key_id}", response_model=UpstreamKeyOut)
async def get_upstream_key(key_id: int, session: SessionDep):
    record = await session.get(UpstreamKey, key_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    return UpstreamKeyOut.from_orm(record)


@router.patch("/upstream-keys/{key_id}", response_model=UpstreamKeyOut)
async def update_upstream_key(key_id: int, payload: UpstreamKeyUpdate, session: SessionDep):
    record = await session.get(UpstreamKey, key_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    if payload.name is not None:
        record.name = payload.name
    if payload.notes is not None:
        record.notes = payload.notes
    if payload.priority is not None:
        record.priority = payload.priority
    if payload.weight is not None:
        record.weight = payload.weight
    if payload.rpm_limit is not None:
        record.rpm_limit = payload.rpm_limit
    if payload.enabled is not None:
        await upstream_svc.set_enabled(session, key_id, payload.enabled)
        record = await session.get(UpstreamKey, key_id)
    await session.flush()
    return UpstreamKeyOut.from_orm(record)


@router.delete("/upstream-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upstream_key(key_id: int, session: SessionDep):
    ok = await upstream_svc.delete_key(session, key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")


@router.post("/upstream-keys/{key_id}/probe")
async def manual_probe(key_id: int) -> dict[str, Any]:
    result = await balance_svc.probe_one(key_id)
    if result is None:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "ok": result.ok, "balance_usd": result.balance_usd,
        "suspend_state": result.suspend_state, "account_state": result.account_state,
        "new_status": result.new_status.value if result.new_status else None,
        "disable_reason": result.disable_reason,
        "error": result.error, "latency_ms": result.latency_ms,
    }


@router.post("/probe-now")
async def trigger_probe_round() -> dict[str, int]:
    return await balance_svc.run_probe_round()


@router.post("/upstream-keys/{key_id}/refresh-balance")
async def refresh_balance_one(key_id: int) -> dict[str, Any]:
    """轻量级手动刷新单把 Key 的余额。

    与 /probe 的区别：
    - 不会因为余额低 / suspendState 而把 Key 标记为 auto_disabled
    - 不写 ProbeHistory（不污染探针日志）
    - 返回新旧余额对比 + 占用率，方便 UI 直观展示
    """
    result = await balance_svc.refresh_balance_one(key_id)
    if result is None:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "key_id": result.key_id,
        "key_preview": result.key_preview,
        "ok": result.ok,
        "previous_balance_usd": result.previous_balance_usd,
        "previous_used_usd": result.previous_used_usd,
        "balance_usd": result.balance_usd,
        "monthly_spend_limit_usd": result.monthly_spend_limit_usd,
        "monthly_spend_used_usd": result.monthly_spend_used_usd,
        "balance_percent": result.balance_percent,
        "used_percent": result.used_percent,
        "delta_balance_usd": result.delta_balance_usd,
        "delta_used_usd": result.delta_used_usd,
        "suspend_state": result.suspend_state,
        "account_state": result.account_state,
        "error": result.error,
        "latency_ms": result.latency_ms,
    }


@router.post("/upstream-keys/refresh-balances")
async def refresh_balances_all() -> dict[str, Any]:
    """批量并发刷新所有可调度 Key 的余额（10 并发）。"""
    return await balance_svc.refresh_balance_all()


# ============================= API Keys (下游 sk-fwr-) =============================


class ApiKeyOut(BaseModel):
    id: int
    label: str
    note: str | None
    token_preview: str
    can_reveal: bool = False  # 是否可以直接复制完整 token（v4 之前的旧 key 为 False）
    status: str
    expires_at: datetime | None
    unlimited_quota: bool
    remaining_quota_usd: float
    used_quota_usd: float
    allowed_models: list[str] | None
    allowed_ips: list[str] | None
    max_tokens_per_request: int
    rpm_limit: int
    stream_enabled: bool
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    last_used_at: datetime | None
    created_at: datetime

    @classmethod
    def from_orm(cls, t: ApiKey) -> "ApiKeyOut":
        return cls(
            id=t.id, label=t.label, note=t.note,
            token_preview=t.token_preview, can_reveal=t.token_encrypted is not None,
            status=t.status.value,
            expires_at=t.expires_at, unlimited_quota=t.unlimited_quota,
            remaining_quota_usd=t.remaining_quota_usd, used_quota_usd=t.used_quota_usd,
            allowed_models=t.allowed_models, allowed_ips=t.allowed_ips,
            max_tokens_per_request=t.max_tokens_per_request, rpm_limit=t.rpm_limit,
            stream_enabled=t.stream_enabled, total_requests=t.total_requests,
            total_input_tokens=t.total_input_tokens, total_output_tokens=t.total_output_tokens,
            last_used_at=t.last_used_at, created_at=t.created_at,
        )


class ApiKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    note: str | None = None
    expires_at: datetime | None = None
    unlimited_quota: bool = True
    remaining_quota_usd: float = 0.0
    allowed_models: list[str] | None = None
    allowed_ips: list[str] | None = None
    max_tokens_per_request: int = 0
    rpm_limit: int = 0
    stream_enabled: bool = True


class ApiKeyUpdate(BaseModel):
    label: str | None = None
    note: str | None = None
    expires_at: datetime | None = None
    unlimited_quota: bool | None = None
    remaining_quota_usd: float | None = None
    allowed_models: list[str] | None = None
    allowed_ips: list[str] | None = None
    max_tokens_per_request: int | None = None
    rpm_limit: int | None = None
    stream_enabled: bool | None = None
    status: str | None = None


class ApiKeyCreated(ApiKeyOut):
    token: str = Field(description="明文 token；仅在创建/旋转时返回一次")


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(session: SessionDep):
    stmt = select(ApiKey).order_by(desc(ApiKey.id))
    rows = list((await session.execute(stmt)).scalars().all())
    return [ApiKeyOut.from_orm(r) for r in rows]


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(payload: ApiKeyCreate, session: SessionDep):
    token = generate_user_token()
    record = ApiKey(
        label=payload.label, note=payload.note,
        token_hash=hash_token(token),
        token_preview=f"{token[:10]}...{token[-4:]}",
        token_encrypted=encrypt_key(token),  # Fernet 加密存明文，后续可复制
        status=ApiKeyStatus.active,
        expires_at=payload.expires_at,
        unlimited_quota=payload.unlimited_quota,
        remaining_quota_usd=payload.remaining_quota_usd,
        allowed_models=payload.allowed_models, allowed_ips=payload.allowed_ips,
        max_tokens_per_request=payload.max_tokens_per_request,
        rpm_limit=payload.rpm_limit, stream_enabled=payload.stream_enabled,
    )
    session.add(record)
    await session.flush()
    out = ApiKeyOut.from_orm(record).model_dump()
    out["token"] = token
    return ApiKeyCreated(**out)


@router.patch("/api-keys/{key_id}", response_model=ApiKeyOut)
async def update_api_key(key_id: int, payload: ApiKeyUpdate, session: SessionDep):
    record = await session.get(ApiKey, key_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "status" and value is not None:
            try:
                record.status = ApiKeyStatus(value)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"invalid status: {value}") from None
            continue
        setattr(record, field, value)
    await session.flush()
    return ApiKeyOut.from_orm(record)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(key_id: int, session: SessionDep):
    record = await session.get(ApiKey, key_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    from app.api.deps import invalidate_api_key_cache
    invalidate_api_key_cache(record.token_hash)
    await session.delete(record)


@router.post("/api-keys/{key_id}/rotate", response_model=ApiKeyCreated)
async def rotate_api_key(key_id: int, session: SessionDep):
    record = await session.get(ApiKey, key_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    from app.api.deps import invalidate_api_key_cache
    invalidate_api_key_cache(record.token_hash)
    new_token = generate_user_token()
    record.token_hash = hash_token(new_token)
    record.token_preview = f"{new_token[:10]}...{new_token[-4:]}"
    record.token_encrypted = encrypt_key(new_token)
    await session.flush()
    out = ApiKeyOut.from_orm(record).model_dump()
    out["token"] = new_token
    return ApiKeyCreated(**out)


@router.get("/api-keys/{key_id}/reveal")
async def reveal_api_key(key_id: int, session: SessionDep) -> dict:
    """返回明文 token（仅 v4 之后创建/旋转过的 Key 有密文可解）。"""
    record = await session.get(ApiKey, key_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    if record.token_encrypted is None:
        raise HTTPException(
            status_code=410,
            detail={"error": {"message": "Token not retrievable (created before v4). Rotate to get a new copyable token.", "type": "not_retrievable"}},
        )
    try:
        plaintext = decrypt_key(record.token_encrypted)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"decrypt failed: {e}") from None
    return {"token": plaintext, "preview": record.token_preview}


# ============================= Logs / Stats =============================


@router.get("/logs/requests")
async def list_request_logs(
    session: SessionDep,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    upstream_key_id: int | None = None,
    api_key_id: int | None = None,
    status_code: int | None = None,
    status_min: int | None = None,
    status_max: int | None = None,
    model: str | None = None,
    stream: str | None = None,
    request_id: str | None = None,
    period: str | None = Query(None, description="1h / 24h / 7d / all"),
) -> dict[str, Any]:
    stmt = select(RequestLog).order_by(desc(RequestLog.id))
    if upstream_key_id is not None:
        stmt = stmt.where(RequestLog.upstream_key_id == upstream_key_id)
    if api_key_id is not None:
        stmt = stmt.where(RequestLog.api_key_id == api_key_id)
    if status_code is not None:
        stmt = stmt.where(RequestLog.status_code == status_code)
    if status_min is not None:
        stmt = stmt.where(RequestLog.status_code >= status_min)
    if status_max is not None:
        stmt = stmt.where(RequestLog.status_code <= status_max)
    if model:
        stmt = stmt.where(RequestLog.public_model == model)
    if stream is not None:
        if stream.lower() in ("true", "1"):
            stmt = stmt.where(RequestLog.stream.is_(True))
        elif stream.lower() in ("false", "0"):
            stmt = stmt.where(RequestLog.stream.is_(False))
    if request_id:
        stmt = stmt.where(RequestLog.request_id.like(f"%{request_id}%"))
    if period and period != "all":
        delta = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}.get(period)
        if delta:
            since = datetime.now(timezone.utc) - delta
            stmt = stmt.where(RequestLog.created_at >= since)
    rows = list((await session.execute(stmt.limit(limit).offset(offset))).scalars().all())
    return {
        "items": [
            {
                "id": r.id, "request_id": r.request_id,
                "api_key_id": r.api_key_id, "api_key_label": r.api_key_label,
                "api_key_preview": r.api_key_preview,
                "upstream_key_id": r.upstream_key_id,
                "upstream_key_preview": r.upstream_key_preview,
                "public_model": r.public_model, "endpoint": r.endpoint, "stream": r.stream,
                "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                "cached_tokens": r.cached_tokens, "total_tokens": r.total_tokens,
                "raw_cost_usd": r.raw_cost_usd, "billed_cost_usd": r.billed_cost_usd,
                "rate_multiplier": r.rate_multiplier,
                "status_code": r.status_code, "error_code": r.error_code,
                "retry_count": r.retry_count,
                "ttft_ms": r.ttft_ms, "latency_ms": r.latency_ms,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "limit": limit, "offset": offset,
    }


@router.get("/logs/probes")
async def list_probe_logs(
    session: SessionDep,
    limit: int = Query(50, ge=1, le=500),
    upstream_key_id: int | None = None,
) -> dict[str, Any]:
    stmt = select(ProbeHistory).order_by(desc(ProbeHistory.id))
    if upstream_key_id is not None:
        stmt = stmt.where(ProbeHistory.upstream_key_id == upstream_key_id)
    rows = list((await session.execute(stmt.limit(limit))).scalars().all())
    return {
        "items": [
            {
                "id": r.id, "upstream_key_id": r.upstream_key_id,
                "upstream_key_preview": r.upstream_key_preview,
                "success": r.success, "balance_usd": r.balance_usd,
                "monthly_spend_limit_usd": r.monthly_spend_limit_usd,
                "monthly_spend_used_usd": r.monthly_spend_used_usd,
                "suspend_state": r.suspend_state, "account_state": r.account_state,
                "error_message": r.error_message, "latency_ms": r.latency_ms,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@router.get("/stats/overview")
async def stats_overview(session: SessionDep) -> dict[str, Any]:
    """Dashboard 5 秒一刷，优化前是 8 次独立 COUNT/SUM；现在合并为 3 次。

    upstream_keys 表行数小（< 1000），一次扫即可用 CASE WHEN 出所有桶。
    request_logs 行数大，全表 COUNT 慢，改为只数最近 24h 的（实用且快）。
    """
    from sqlalchemy import case

    now = datetime.now(timezone.utc)
    # 1) upstream_keys 一次 SQL 出 6 个聚合（CASE WHEN）
    upstream_row = (await session.execute(
        select(
            func.count(UpstreamKey.id).label("total"),
            func.sum(
                case((
                    (UpstreamKey.status == UpstreamKeyStatus.active) & UpstreamKey.enabled.is_(True),
                    1,
                ), else_=0)
            ).label("active"),
            func.sum(
                case((UpstreamKey.status == UpstreamKeyStatus.auto_disabled, 1), else_=0)
            ).label("auto_disabled"),
            func.sum(
                case((
                    (UpstreamKey.cooldown_until.isnot(None)) & (UpstreamKey.cooldown_until > now),
                    1,
                ), else_=0)
            ).label("in_cooldown"),
            func.coalesce(func.sum(
                case((
                    (UpstreamKey.status == UpstreamKeyStatus.active) & UpstreamKey.enabled.is_(True),
                    UpstreamKey.balance_usd,
                ), else_=0)
            ), 0.0).label("total_balance"),
            func.coalesce(func.sum(UpstreamKey.monthly_spend_used_usd), 0.0).label("total_used"),
        )
    )).first()

    # 2) api_keys 总数 — 1 次 SQL
    api_keys_total = await session.scalar(select(func.count(ApiKey.id)))

    # 3) request_logs 最近 24h 计数（避免全表扫，老日志被 cleaner 清掉后 COUNT 也快）
    requests_total = await session.scalar(
        select(func.count(RequestLog.id)).where(
            RequestLog.created_at >= now - timedelta(hours=24)
        )
    )

    return {
        "upstream": {
            "total": int(upstream_row.total or 0),
            "active": int(upstream_row.active or 0),
            "auto_disabled": int(upstream_row.auto_disabled or 0),
            "in_cooldown": int(upstream_row.in_cooldown or 0),
            "total_balance_usd": float(upstream_row.total_balance or 0.0),
            "total_used_usd": float(upstream_row.total_used or 0.0),
        },
        "api_keys_total": int(api_keys_total or 0),
        "requests_total_24h": int(requests_total or 0),
    }


def _utc_day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/stats/today")
async def stats_today(session: SessionDep) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = _utc_day_start(now)
    stmt = select(
        func.count(RequestLog.id),
        func.coalesce(func.sum(RequestLog.prompt_tokens), 0),
        func.coalesce(func.sum(RequestLog.completion_tokens), 0),
        func.coalesce(func.sum(RequestLog.billed_cost_usd), 0.0),
    ).where(RequestLog.created_at >= start)
    row = (await session.execute(stmt)).first()
    return {
        "requests": int(row[0] or 0),
        "prompt_tokens": int(row[1] or 0),
        "completion_tokens": int(row[2] or 0),
        "total_tokens": int((row[1] or 0) + (row[2] or 0)),
        "billed_cost_usd": float(row[3] or 0.0),
        "since": start.isoformat(),
    }


@router.get("/stats/top")
async def stats_top(
    session: SessionDep,
    dimension: Literal["api_key", "model", "upstream"] = "api_key",
    period_hours: int = Query(24, ge=1, le=24 * 30),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=period_hours)
    if dimension == "api_key":
        group_col = RequestLog.api_key_label
    elif dimension == "model":
        group_col = RequestLog.public_model
    else:
        group_col = RequestLog.upstream_key_preview
    stmt = (
        select(
            group_col.label("key"),
            func.count(RequestLog.id).label("requests"),
            func.coalesce(func.sum(RequestLog.prompt_tokens + RequestLog.completion_tokens), 0).label("tokens"),
            func.coalesce(func.sum(RequestLog.billed_cost_usd), 0.0).label("cost"),
        )
        .where(RequestLog.created_at >= since)
        .where(group_col.isnot(None))
        .group_by(group_col)
        .order_by(desc("cost"), desc("requests"))
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).all())
    return {
        "dimension": dimension,
        "period_hours": period_hours,
        "since": since.isoformat(),
        "items": [
            {"key": r.key, "requests": int(r.requests), "tokens": int(r.tokens), "cost_usd": float(r.cost)}
            for r in rows
        ],
    }


@router.get("/stats/request-trace")
async def stats_request_trace(
    session: SessionDep,
    minutes: int = Query(60, ge=1, le=24 * 60),
    limit: int = Query(500, ge=1, le=5000),
    upstream_key_id: int | None = None,
    api_key_id: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """请求轨迹散点：返回最近 N 分钟的调用记录，按时间正序。

    给散点图用：横轴 created_at，纵轴 upstream_key_id，颜色 status_code，大小 latency_ms。
    """
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    stmt = (
        select(RequestLog)
        .where(RequestLog.created_at >= since)
        .order_by(desc(RequestLog.created_at))
        .limit(limit)
    )
    if upstream_key_id is not None:
        stmt = stmt.where(RequestLog.upstream_key_id == upstream_key_id)
    if api_key_id is not None:
        stmt = stmt.where(RequestLog.api_key_id == api_key_id)
    if model:
        stmt = stmt.where(RequestLog.public_model == model)
    rows = list((await session.execute(stmt)).scalars().all())

    # 顺便取所有用过的 upstream_keys 元信息（用于纵轴标签）
    key_ids = {r.upstream_key_id for r in rows if r.upstream_key_id is not None}
    keys_info: dict[int, dict[str, Any]] = {}
    if key_ids:
        ks = list((await session.execute(
            select(UpstreamKey).where(UpstreamKey.id.in_(key_ids))
        )).scalars().all())
        keys_info = {
            k.id: {"id": k.id, "name": k.name, "preview": k.key_preview}
            for k in ks
        }

    # 当前调度策略（让 UI 一并展示）
    from app.services import settings as settings_svc
    strategy = settings_svc.get("scheduler.strategy", "weighted_random")

    return {
        "minutes": minutes,
        "since": since.isoformat(),
        "strategy": strategy,
        "total": len(rows),
        "keys": list(keys_info.values()),
        "points": [
            {
                "ts": r.created_at.isoformat() if r.created_at else None,
                "request_id": r.request_id,
                "upstream_key_id": r.upstream_key_id,
                "upstream_key_preview": r.upstream_key_preview,
                "api_key_label": r.api_key_label,
                "public_model": r.public_model,
                "status_code": r.status_code,
                "stream": r.stream,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "latency_ms": r.latency_ms,
                "ttft_ms": r.ttft_ms,
                "retry_count": r.retry_count,
                "billed_cost_usd": r.billed_cost_usd,
            }
            for r in rows
        ],
    }


@router.get("/stats/flow-sankey")
async def stats_flow_sankey(
    session: SessionDep,
    hours: int = Query(24, ge=1, le=168),
) -> dict[str, Any]:
    """流量桑基图：聚合 api_key_label → public_model → upstream_key_preview 三层流向。

    返回 { nodes: [{name, layer}], links: [{source, target, value}] }，给 ECharts sankey 直接吃。
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = (
        select(
            RequestLog.api_key_label,
            RequestLog.public_model,
            RequestLog.upstream_key_preview,
            func.count(RequestLog.id),
        )
        .where(RequestLog.created_at >= since)
        .where(RequestLog.upstream_key_id.isnot(None))
        .group_by(
            RequestLog.api_key_label,
            RequestLog.public_model,
            RequestLog.upstream_key_preview,
        )
    )
    rows = list((await session.execute(stmt)).all())

    # 节点 + 链接（去重）
    node_set: set[tuple[str, str]] = set()  # (name, layer)
    layer1_links: dict[tuple[str, str], int] = {}  # (api_key_label, public_model) -> count
    layer2_links: dict[tuple[str, str], int] = {}  # (public_model, upstream_preview) -> count

    for ak, mdl, up, cnt in rows:
        ak = ak or "(unknown)"
        mdl = mdl or "(unknown)"
        up = up or "(unknown)"
        node_set.add((f"api:{ak}", "api_key"))
        node_set.add((f"model:{mdl}", "model"))
        node_set.add((f"key:{up}", "upstream"))
        layer1_links[(f"api:{ak}", f"model:{mdl}")] = layer1_links.get((f"api:{ak}", f"model:{mdl}"), 0) + int(cnt)
        layer2_links[(f"model:{mdl}", f"key:{up}")] = layer2_links.get((f"model:{mdl}", f"key:{up}"), 0) + int(cnt)

    nodes = [{"name": n, "layer": l} for n, l in sorted(node_set, key=lambda x: (x[1], x[0]))]
    links = (
        [{"source": s, "target": t, "value": v} for (s, t), v in layer1_links.items()]
        + [{"source": s, "target": t, "value": v} for (s, t), v in layer2_links.items()]
    )

    return {
        "hours": hours,
        "since": since.isoformat(),
        "nodes": nodes,
        "links": links,
    }


@router.get("/stats/timeseries")
async def stats_timeseries(
    session: SessionDep,
    period_hours: int = Query(24, ge=1, le=24 * 7),
    bucket: Literal["hour", "minute"] = "hour",
) -> dict[str, Any]:
    """SQL GROUP BY 直接出桶（替代旧版的"拉全量再 Python 分桶"）。

    数据量大时，1 周百万行的全量 SELECT 改为 SQL 内 strftime/DATE_TRUNC 聚合，
    返回到 Python 层只有 N 桶（24h*60=1440 个 minute 桶，或 24-168 个 hour 桶）。
    传输量减少 1000+ 倍，DB 也只做一次顺序扫 + 哈希聚合。
    """
    from app.db import _settings as _db_settings  # is_sqlite

    since = datetime.now(timezone.utc) - timedelta(hours=period_hours)
    # 兼容 SQLite (strftime) 和 PostgreSQL (date_trunc) 的桶化表达式
    if _db_settings.is_sqlite:
        if bucket == "minute":
            bucket_col = func.strftime("%Y-%m-%dT%H:%M:00", RequestLog.created_at)
        else:
            bucket_col = func.strftime("%Y-%m-%dT%H:00:00", RequestLog.created_at)
    else:
        # PostgreSQL
        bucket_col = func.date_trunc(bucket, RequestLog.created_at)

    stmt = (
        select(
            bucket_col.label("ts"),
            func.count(RequestLog.id).label("requests"),
            func.coalesce(
                func.sum(RequestLog.prompt_tokens + RequestLog.completion_tokens), 0
            ).label("tokens"),
            func.coalesce(func.sum(RequestLog.billed_cost_usd), 0.0).label("cost_usd"),
            func.sum(
                # 错误 = status_code >= 400
                # SQLite 不支持 case 表达式里的 boolean 算术，必须用 case
                func.coalesce(
                    func.cast(RequestLog.status_code >= 400, type_=RequestLog.id.type), 0
                )
            ).label("errors"),
        )
        .where(RequestLog.created_at >= since)
        .group_by(bucket_col)
        .order_by(bucket_col)
    )
    rows = list((await session.execute(stmt)).all())
    series = [
        {
            "ts": (r.ts if isinstance(r.ts, str) else r.ts.isoformat()),
            "requests": int(r.requests or 0),
            "tokens": int(r.tokens or 0),
            "cost_usd": float(r.cost_usd or 0.0),
            "errors": int(r.errors or 0),
        }
        for r in rows
    ]
    return {"period_hours": period_hours, "bucket": bucket, "series": series}
