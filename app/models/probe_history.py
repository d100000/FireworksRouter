from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProbeHistory(Base):
    __tablename__ = "probe_history"
    __table_args__ = (
        Index("ix_probe_history_key", "upstream_key_id"),
        Index("ix_probe_history_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upstream_key_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upstream_key_preview: Mapped[str] = mapped_column(String(32), nullable=False)

    success: Mapped[str] = mapped_column(String(16), nullable=False)  # "ok" / "error"
    balance_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    monthly_spend_limit_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    monthly_spend_used_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    suspend_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_state: Mapped[str | None] = mapped_column(String(64), nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
