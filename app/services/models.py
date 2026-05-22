"""模型解析、同步与计费帮助函数。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Model, ModelCategory, ModelStatus
from app.services import fireworks as fw


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

    - 新模型：插入，status=disabled，等管理员手动启用并填价
    - 已存在：更新 context_length / supports_* 这类元数据
    """
    items = await fw.list_models(api_key)
    created = 0
    updated = 0
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
        if existing is None:
            session.add(
                Model(
                    public_name=_public_name_from_path(path),
                    fireworks_path=path,
                    category=category,
                    context_length=context_length,
                    supports_streaming=supports_chat,
                    supports_tools=supports_tools,
                    supports_vision=supports_vision,
                    status=ModelStatus.disabled,
                )
            )
            created += 1
        else:
            existing.category = category
            existing.context_length = context_length or existing.context_length
            existing.supports_streaming = supports_chat or existing.supports_streaming
            existing.supports_tools = supports_tools or existing.supports_tools
            existing.supports_vision = supports_vision or existing.supports_vision
            updated += 1
    return {"total": len(items), "created": created, "updated": updated}
