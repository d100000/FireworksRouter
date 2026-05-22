from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class RequestLog(Base):
    __tablename__ = "request_logs"
    __table_args__ = (
        Index("ix_request_logs_created_at", "created_at"),
        Index("ix_request_logs_api_key", "api_key_id"),
        Index("ix_request_logs_upstream", "upstream_key_id"),
        Index("ix_request_logs_request_id", "request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_key_label: Mapped[str | None] = mapped_column(String(64), nullable=True)

    upstream_key_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upstream_key_preview: Mapped[str | None] = mapped_column(String(32), nullable=True)

    model_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    public_model: Mapped[str] = mapped_column(String(255), nullable=False)
    upstream_model: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    stream: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    raw_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    billed_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rate_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    status_code: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ttft_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
