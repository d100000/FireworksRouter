"""模型价格目录管理 API。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.api.deps import SessionDep, require_admin
from app.models import (
    ModelPriceCatalog,
    PriceMatchType,
    PriceSource,
    PriceUnit,
)
from app.services import price_catalog as pc

router = APIRouter(
    prefix="/admin/price-catalog",
    tags=["admin/price-catalog"],
    dependencies=[Depends(require_admin)],
)


class PriceItemOut(BaseModel):
    id: int
    pattern: str
    match_type: str
    input_per_1m: float
    output_per_1m: float
    cached_input_per_1m: float
    per_image_usd: float
    per_step_usd: float
    unit: str
    source: str
    priority: int
    enabled: bool
    note: str | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, r: ModelPriceCatalog) -> "PriceItemOut":
        return cls(
            id=r.id, pattern=r.pattern, match_type=r.match_type.value,
            input_per_1m=r.input_per_1m, output_per_1m=r.output_per_1m,
            cached_input_per_1m=r.cached_input_per_1m,
            per_image_usd=r.per_image_usd, per_step_usd=r.per_step_usd,
            unit=r.unit.value, source=r.source.value,
            priority=r.priority, enabled=bool(r.enabled),
            note=r.note, last_synced_at=r.last_synced_at,
            created_at=r.created_at, updated_at=r.updated_at,
        )


class PriceItemCreate(BaseModel):
    pattern: str = Field(min_length=1, max_length=255)
    match_type: str = "contains"
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0
    cached_input_per_1m: float = 0.0
    per_image_usd: float = 0.0
    per_step_usd: float = 0.0
    unit: str = "per_token"
    priority: int = 10
    enabled: bool = True
    note: str | None = None


class PriceItemUpdate(BaseModel):
    pattern: str | None = None
    match_type: str | None = None
    input_per_1m: float | None = None
    output_per_1m: float | None = None
    cached_input_per_1m: float | None = None
    per_image_usd: float | None = None
    per_step_usd: float | None = None
    unit: str | None = None
    priority: int | None = None
    enabled: bool | None = None
    note: str | None = None


# ============================= CRUD =============================


@router.get("", response_model=list[PriceItemOut])
async def list_prices(
    session: SessionDep,
    source: str | None = Query(None, description="过滤来源"),
    pattern: str | None = Query(None, description="模糊匹配 pattern"),
):
    stmt = select(ModelPriceCatalog).order_by(
        desc(ModelPriceCatalog.priority), desc(ModelPriceCatalog.id)
    )
    if source:
        stmt = stmt.where(ModelPriceCatalog.source == PriceSource(source))
    if pattern:
        stmt = stmt.where(ModelPriceCatalog.pattern.ilike(f"%{pattern}%"))
    rows = list((await session.execute(stmt)).scalars().all())
    return [PriceItemOut.from_orm(r) for r in rows]


@router.post("", response_model=PriceItemOut, status_code=status.HTTP_201_CREATED)
async def create_price(payload: PriceItemCreate, session: SessionDep):
    try:
        record = ModelPriceCatalog(
            pattern=payload.pattern,
            match_type=PriceMatchType(payload.match_type),
            input_per_1m=payload.input_per_1m,
            output_per_1m=payload.output_per_1m,
            cached_input_per_1m=payload.cached_input_per_1m,
            per_image_usd=payload.per_image_usd,
            per_step_usd=payload.per_step_usd,
            unit=PriceUnit(payload.unit),
            source=PriceSource.manual,
            priority=payload.priority,
            enabled=payload.enabled,
            note=payload.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid enum: {e}") from None
    session.add(record)
    await session.flush()
    return PriceItemOut.from_orm(record)


@router.patch("/{item_id}", response_model=PriceItemOut)
async def update_price(item_id: int, payload: PriceItemUpdate, session: SessionDep):
    record = await session.get(ModelPriceCatalog, item_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    data = payload.model_dump(exclude_unset=True)
    if "match_type" in data:
        record.match_type = PriceMatchType(data.pop("match_type"))
    if "unit" in data:
        record.unit = PriceUnit(data.pop("unit"))
    for k, v in data.items():
        setattr(record, k, v)
    await session.flush()
    return PriceItemOut.from_orm(record)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_price(item_id: int, session: SessionDep):
    record = await session.get(ModelPriceCatalog, item_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    await session.delete(record)


# ============================= 同步 / 种子 =============================


@router.post("/sync-litellm")
async def sync_from_litellm(
    session: SessionDep,
    overwrite_existing: bool = Query(False, description="是否覆盖 source=litellm 之外的条目"),
) -> dict[str, Any]:
    """从 LiteLLM GitHub 拉 Fireworks 价格 upsert 到 DB。

    LiteLLM 仓库 https://github.com/BerriAI/litellm 维护的 model_prices_and_context_window.json
    含 278+ Fireworks 相关条目（截至 2026-05），社区每周更新。
    """
    result = await pc.sync_from_litellm(session, overwrite_existing=overwrite_existing)
    return {
        "fetched_total": result.fetched_total,
        "fireworks_matched": result.fireworks_matched,
        "created": result.created,
        "updated": result.updated,
        "skipped": result.skipped,
        "errors": result.errors,
    }


@router.post("/seed")
async def seed_initial(session: SessionDep) -> dict[str, int]:
    """把 hardcoded KNOWN_PRICES 灌入 DB（幂等：已存在 seed 条目则跳过）。"""
    inserted = await pc.seed_initial(session)
    return {"inserted": inserted}
