from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class ModelCategory(str, enum.Enum):
    chat = "chat"
    completion = "completion"
    embedding = "embedding"
    image = "image"
    audio = "audio"
    rerank = "rerank"
    vision = "vision"
    other = "other"


class ModelStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"


class Model(Base):
    __tablename__ = "models"
    __table_args__ = (
        Index("ix_models_public_name", "public_name", unique=True),
        Index("ix_models_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    public_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    fireworks_path: Mapped[str] = mapped_column(String(255), nullable=False)

    category: Mapped[ModelCategory] = mapped_column(
        SAEnum(ModelCategory, native_enum=False, length=16),
        default=ModelCategory.chat,
        nullable=False,
    )
    status: Mapped[ModelStatus] = mapped_column(
        SAEnum(ModelStatus, native_enum=False, length=16),
        default=ModelStatus.disabled,
        nullable=False,
    )

    context_length: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 价格按"每百万 token 美元"存储
    input_price_per_1m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    output_price_per_1m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cached_input_price_per_1m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    supports_streaming: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_reasoning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def compute_cost(self, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float:
        """按真实 token 数计算成本（美元）。"""
        billable_prompt = max(0, prompt_tokens - cached_tokens)
        return (
            billable_prompt * self.input_price_per_1m / 1_000_000.0
            + completion_tokens * self.output_price_per_1m / 1_000_000.0
            + cached_tokens * self.cached_input_price_per_1m / 1_000_000.0
        )
