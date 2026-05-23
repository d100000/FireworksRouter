from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from app.api.deps import SessionDep, require_admin
from app.crypto import decrypt_key
from app.models import Model, ModelCategory, ModelStatus, UpstreamKey, UpstreamKeyStatus
from app.services import models as models_svc

router = APIRouter(
    prefix="/admin",
    tags=["admin/models"],
    dependencies=[Depends(require_admin)],
)


class ModelOut(BaseModel):
    id: int
    public_name: str
    fireworks_path: str
    category: str
    status: str
    context_length: int
    max_output_tokens: int
    input_price_per_1m: float
    output_price_per_1m: float
    cached_input_price_per_1m: float
    supports_streaming: bool
    supports_tools: bool
    supports_vision: bool
    supports_reasoning: bool
    sort_order: int
    description: str | None
    created_at: datetime

    @classmethod
    def from_orm(cls, m: Model) -> "ModelOut":
        return cls(
            id=m.id, public_name=m.public_name, fireworks_path=m.fireworks_path,
            category=m.category.value, status=m.status.value,
            context_length=m.context_length, max_output_tokens=m.max_output_tokens,
            input_price_per_1m=m.input_price_per_1m,
            output_price_per_1m=m.output_price_per_1m,
            cached_input_price_per_1m=m.cached_input_price_per_1m,
            supports_streaming=m.supports_streaming, supports_tools=m.supports_tools,
            supports_vision=m.supports_vision, supports_reasoning=m.supports_reasoning,
            sort_order=m.sort_order, description=m.description, created_at=m.created_at,
        )


class ModelCreate(BaseModel):
    public_name: str = Field(min_length=1, max_length=128)
    fireworks_path: str = Field(min_length=1, max_length=255)
    category: str = "chat"
    status: str = "disabled"
    context_length: int = 0
    max_output_tokens: int = 0
    input_price_per_1m: float = 0.0
    output_price_per_1m: float = 0.0
    cached_input_price_per_1m: float = 0.0
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    supports_reasoning: bool = False
    sort_order: int = 0
    description: str | None = None


class ModelUpdate(BaseModel):
    public_name: str | None = None
    fireworks_path: str | None = None
    category: str | None = None
    status: str | None = None
    context_length: int | None = None
    max_output_tokens: int | None = None
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None
    cached_input_price_per_1m: float | None = None
    supports_streaming: bool | None = None
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_reasoning: bool | None = None
    sort_order: int | None = None
    description: str | None = None


def _coerce_category(value: str) -> ModelCategory:
    try:
        return ModelCategory(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid category: {value}") from None


def _coerce_status(value: str) -> ModelStatus:
    try:
        return ModelStatus(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid status: {value}") from None


@router.get("/models", response_model=list[ModelOut])
async def list_models(session: SessionDep, status_filter: str | None = None):
    stmt = select(Model).order_by(Model.sort_order, Model.id)
    if status_filter:
        stmt = stmt.where(Model.status == _coerce_status(status_filter))
    rows = list((await session.execute(stmt)).scalars().all())
    return [ModelOut.from_orm(m) for m in rows]


@router.post("/models", response_model=ModelOut, status_code=status.HTTP_201_CREATED)
async def create_model(payload: ModelCreate, session: SessionDep):
    data = payload.model_dump()
    data["category"] = _coerce_category(data["category"])
    data["status"] = _coerce_status(data["status"])
    record = Model(**data)
    session.add(record)
    try:
        await session.flush()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"insert failed: {e}") from None
    return ModelOut.from_orm(record)


@router.patch("/models/{model_id}", response_model=ModelOut)
async def update_model(model_id: int, payload: ModelUpdate, session: SessionDep):
    record = await session.get(Model, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    data = payload.model_dump(exclude_unset=True)
    if "category" in data and data["category"] is not None:
        record.category = _coerce_category(data.pop("category"))
    if "status" in data and data["status"] is not None:
        record.status = _coerce_status(data.pop("status"))
    for field, value in data.items():
        if value is not None:
            setattr(record, field, value)
    await session.flush()
    return ModelOut.from_orm(record)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(model_id: int, session: SessionDep):
    record = await session.get(Model, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    await session.delete(record)


class BatchToggleIn(BaseModel):
    ids: list[int] = Field(min_length=1)
    status: str = "active"


@router.post("/models/batch-status")
async def batch_set_status(payload: BatchToggleIn, session: SessionDep) -> dict[str, int]:
    """批量启用/禁用模型。"""
    try:
        new_status = ModelStatus(payload.status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid status: {payload.status}") from None
    updated = 0
    for mid in payload.ids:
        m = await session.get(Model, mid)
        if m is None:
            continue
        m.status = new_status
        updated += 1
    return {"updated": updated, "requested": len(payload.ids)}


@router.post("/models/sync")
async def sync_models(session: SessionDep) -> dict[str, Any]:
    """从一把可用的上游 Key 拉取 Fireworks 模型列表并同步入库。

    优先用 status=active 的 Key；若全部 active 失败 / 没有 active，会回退尝试
    auto_disabled / unhealthy / testing 状态的 Key（同步只是 GET /v1/models，
    不会对账户产生扣费或负面影响）。
    """
    from sqlalchemy import case as sql_case

    # 一次查询拿到各种状态的 Key 数量 + 第一把可用 Key
    counts_row = (await session.execute(
        select(
            func.count(UpstreamKey.id).label("total"),
            func.sum(sql_case((
                (UpstreamKey.status == UpstreamKeyStatus.active) & UpstreamKey.enabled.is_(True),
                1,
            ), else_=0)).label("active"),
            func.sum(sql_case((UpstreamKey.enabled.is_(False), 1), else_=0)).label("disabled"),
            func.sum(sql_case((UpstreamKey.status == UpstreamKeyStatus.auto_disabled, 1), else_=0)).label("auto_disabled"),
            func.sum(sql_case((UpstreamKey.status == UpstreamKeyStatus.unhealthy, 1), else_=0)).label("unhealthy"),
        )
    )).first()

    total = int(counts_row.total or 0)
    active_n = int(counts_row.active or 0)

    # 0 把 Key — 友好提示去添加
    if total == 0:
        raise HTTPException(
            status_code=400,
            detail={"error": {
                "message": "需要先在「上游 Key 池」添加至少一把 Fireworks API Key 才能同步模型。",
                "type": "no_upstream_keys",
                "details": {"hint": "菜单 → 上游 Key 池 → 添加 Key（粘贴 fw_ 开头的 Fireworks API Key）"}
            }},
        )

    # 有 Key 但全部不可用 — 告诉用户哪些状态
    if active_n == 0:
        status_summary = []
        if (counts_row.disabled or 0): status_summary.append(f"{counts_row.disabled} 把已禁用")
        if (counts_row.auto_disabled or 0): status_summary.append(f"{counts_row.auto_disabled} 把自动禁用")
        if (counts_row.unhealthy or 0): status_summary.append(f"{counts_row.unhealthy} 把不健康")
        summary_text = "、".join(status_summary) if status_summary else "未知状态"
        raise HTTPException(
            status_code=400,
            detail={"error": {
                "message": f"共 {total} 把 Key 但全部不可用（{summary_text}）。请先在「上游 Key 池」检查并启用至少一把。",
                "type": "no_active_upstream_keys",
                "details": {
                    "total": total,
                    "active": 0,
                    "hint": "上游 Key 池 → 点行内「更新余额」或「探针」让 Key 恢复 active 状态",
                },
            }},
        )

    # 有 active Key — 尝试用第一把同步
    candidates = list((await session.execute(
        select(UpstreamKey)
        .where(UpstreamKey.status == UpstreamKeyStatus.active, UpstreamKey.enabled.is_(True))
        .order_by(UpstreamKey.priority.desc(), UpstreamKey.id)
        .limit(3)  # 拿前 3 把做 fallback
    )).scalars().all())

    last_err: Exception | None = None
    for k in candidates:
        try:
            plaintext = decrypt_key(k.key_encrypted)
            return await models_svc.sync_from_fireworks(session, plaintext)
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue

    # 所有 active Key 都同步失败
    raise HTTPException(
        status_code=502,
        detail={"error": {
            "message": f"已尝试 {len(candidates)} 把 active Key 同步模型，全部失败。最后错误：{type(last_err).__name__}: {last_err}",
            "type": "all_upstream_sync_failed",
            "details": {"tried_keys": len(candidates), "last_error": str(last_err)[:200]},
        }},
    )
