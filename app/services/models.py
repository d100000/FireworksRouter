"""模型解析、同步与计费帮助函数。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Model, ModelCategory, ModelStatus, ModelPriceCatalog
from app.services import fireworks as fw
from app.services import price_catalog as pc
from app.services.known_prices import lookup_price as lookup_hardcoded_price


@dataclass
class ResolvedModel:
    record: Model | None
    public_name: str
    upstream_path: str  # 真实传给 Fireworks 的 model 字段


async def resolve(session: AsyncSession, requested_model: str) -> ResolvedModel:
    """根据下游请求里的 model 字段定位一条本地 Model 记录。

    匹配优先级：
    1) 完全匹配 public_name
    2) 完全匹配 fireworks_path
    3) 未匹配：返回 record=None，upstream_path 原样透传
    """
    stmt = select(Model).where(
        or_(Model.public_name == requested_model, Model.fireworks_path == requested_model)
    )
    record = (await session.execute(stmt)).scalar_one_or_none()
    if record is None:
        return ResolvedModel(record=None, public_name=requested_model, upstream_path=requested_model)
    return ResolvedModel(
        record=record,
        public_name=record.public_name,
        upstream_path=record.fireworks_path,
    )


def is_usable(model: Model | None) -> bool:
    if model is None:
        return True  # 透传模式
    return model.status == ModelStatus.active


def _category_guess(supports_chat: bool, model_id: str) -> ModelCategory:
    mid = model_id.lower()
    if any(k in mid for k in ("embed",)):
        return ModelCategory.embedding
    if any(k in mid for k in ("rerank",)):
        return ModelCategory.rerank
    if any(k in mid for k in ("flux", "stable-diffusion", "sdxl", "image")):
        return ModelCategory.image
    if any(k in mid for k in ("whisper", "tts", "audio")):
        return ModelCategory.audio
    if supports_chat:
        return ModelCategory.chat
    return ModelCategory.other


def _public_name_from_path(fireworks_path: str) -> str:
    """从 accounts/fireworks/models/kimi-k2p6 取出最后一段。"""
    return fireworks_path.rsplit("/", 1)[-1]


async def sync_from_fireworks(session: AsyncSession, api_key: str) -> dict[str, int]:
    """从 Fireworks /inference/v1/models 同步模型到本地。

    - 新模型：插入，自动从已知价格表填充定价，命中则 status=active，未命中则 disabled
    - 已存在：更新元数据；价格仅在原本为 0 时才填（不覆盖管理员手填的价格）
    - 返回：total / created / updated / priced（命中价格表的总数）/ unpriced（无价格需手填）
    """
    items = await fw.list_models(api_key)
    created = 0
    updated = 0
    priced = 0
    unpriced = 0

    for it in items:
        path: str = it.get("id") or ""
        if not path:
            continue
        existing = (
            await session.execute(select(Model).where(Model.fireworks_path == path))
        ).scalar_one_or_none()
        category = _category_guess(bool(it.get("supports_chat")), path)
        context_length = int(it.get("context_length") or 0)
        supports_chat = bool(it.get("supports_chat"))
        supports_vision = bool(it.get("supports_image_input"))
        supports_tools = bool(it.get("supports_tools"))

        # 查 DB 价格目录（多源）；DB 没有再 fallback 到 hardcoded KNOWN_PRICES
        catalog_result = await pc.lookup_price(session, path)
        catalog_row = catalog_result.record
        if catalog_row is not None:
            class _PriceShim:
                input_per_1m = catalog_row.input_per_1m
                output_per_1m = catalog_row.output_per_1m
                cached_input_per_1m = catalog_row.cached_input_per_1m
                note = catalog_row.note or f"From {catalog_row.source.value} (pattern={catalog_row.pattern})"
            price = _PriceShim()
            has_known_entry = True
        else:
            price = lookup_hardcoded_price(path)
            has_known_entry = price is not None

        if existing is None:
            # 新增：命中已知表 → 自动 active；未命中 → disabled 等管理员填
            new_status = ModelStatus.active if has_known_entry else ModelStatus.disabled
            session.add(
                Model(
                    public_name=_public_name_from_path(path),
                    fireworks_path=path,
                    category=category,
                    context_length=context_length,
                    supports_streaming=supports_chat,
                    supports_tools=supports_tools,
                    supports_vision=supports_vision,
                    status=new_status,
                    input_price_per_1m=price.input_per_1m if price else 0.0,
                    output_price_per_1m=price.output_per_1m if price else 0.0,
                    cached_input_price_per_1m=price.cached_input_per_1m if price else 0.0,
                    description=price.note if price else None,
                )
            )
            created += 1
            if has_known_entry:
                priced += 1
            else:
                unpriced += 1
        else:
            existing.category = category
            existing.context_length = context_length or existing.context_length
            existing.supports_streaming = supports_chat or existing.supports_streaming
            existing.supports_tools = supports_tools or existing.supports_tools
            existing.supports_vision = supports_vision or existing.supports_vision

            # 价格回填：只在现有价格为 0 时填（不覆盖管理员手动设置的）
            if price is not None:
                if existing.input_price_per_1m == 0 and price.input_per_1m > 0:
                    existing.input_price_per_1m = price.input_per_1m
                if existing.output_price_per_1m == 0 and price.output_per_1m > 0:
                    existing.output_price_per_1m = price.output_per_1m
                if existing.cached_input_price_per_1m == 0 and price.cached_input_per_1m > 0:
                    existing.cached_input_price_per_1m = price.cached_input_per_1m
                if not existing.description and price.note:
                    existing.description = price.note
            updated += 1
            if existing.input_price_per_1m > 0 or existing.output_price_per_1m > 0:
                priced += 1
            else:
                unpriced += 1

    return {
        "total": len(items),
        "created": created,
        "updated": updated,
        "priced": priced,
        "unpriced": unpriced,
    }
