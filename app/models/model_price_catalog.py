from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PriceMatchType(str, enum.Enum):
    exact = "exact"        # public_name 完全相等
    contains = "contains"  # public_name 包含 pattern（pattern in name）
    prefix = "prefix"      # public_name 以 pattern 开头


class PriceUnit(str, enum.Enum):
    per_token = "per_token"       # 文本 LLM：input/output 价 / 1M tokens
    per_image = "per_image"       # 图像生成：按张
    per_step = "per_step"         # 图像扩散：按 step
    per_request = "per_request"   # 按次请求


class PriceSource(str, enum.Enum):
    seed = "seed"                 # 启动时由 KNOWN_PRICES 种子（hardcoded）
    manual = "manual"             # 管理员手动添加 / 修改
    litellm = "litellm"           # 从 LiteLLM 社区价格库同步
    fireworks = "fireworks"       # 从 fireworks.ai 爬取（保留扩展位）


class ModelPriceCatalog(Base):
    """模型价格目录 — 与 models 表解耦，可独立维护 + 多源同步。

    sync_from_fireworks 时按 pattern 查这个表，命中即填到 Model 行的对应价格字段。
    """

    __tablename__ = "model_price_catalog"
    __table_args__ = (
        Index("ix_price_catalog_pattern", "pattern"),
        Index("ix_price_catalog_source", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    pattern: Mapped[str] = mapped_column(String(255), nullable=False)
    match_type: Mapped[PriceMatchType] = mapped_column(
        SAEnum(PriceMatchType, native_enum=False, length=16),
        default=PriceMatchType.contains,
        nullable=False,
    )

    # 价格（每百万 token；图像类记 0，由 unit/per_image_usd 表达）
    input_per_1m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    output_per_1m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cached_input_per_1m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # 非 token 计费时的辅助字段
    per_image_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    per_step_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    unit: Mapped[PriceUnit] = mapped_column(
        SAEnum(PriceUnit, native_enum=False, length=16),
        default=PriceUnit.per_token,
        nullable=False,
    )
    source: Mapped[PriceSource] = mapped_column(
        SAEnum(PriceSource, native_enum=False, length=16),
        default=PriceSource.manual,
        nullable=False,
    )

    # 优先级越大越优先匹配（同 pattern 多源时用）
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    enabled: Mapped[bool] = mapped_column(
        Integer().with_variant(Integer, "sqlite"), default=1, nullable=False
    )

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
