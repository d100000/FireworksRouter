from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class KeyModelStateStatus(str, enum.Enum):
    ready = "ready"
    cooldown = "cooldown"
    blocked = "blocked"


class KeyModelState(Base):
    """per-(upstream_key, model) 维度的状态机。

    比如：一把 Key 对 'kimi-k2p6' 返回了 404 → 只挂起 (key, kimi-k2p6) 12h，
    其它模型仍可正常用此 Key。
    """

    __tablename__ = "key_model_states"
    __table_args__ = (
        Index("ix_key_model_states_pair", "upstream_key_id", "model_id", unique=True),
        Index("ix_key_model_states_cooldown", "cooldown_until"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upstream_key_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # NULL = 适用于所有模型

    status: Mapped[KeyModelStateStatus] = mapped_column(
        SAEnum(KeyModelStateStatus, native_enum=False, length=16),
        default=KeyModelStateStatus.ready,
        nullable=False,
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backoff_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def is_in_cooldown(self) -> bool:
        if self.cooldown_until is None:
            return False
        from datetime import timezone
        return self.cooldown_until > datetime.now(timezone.utc)
