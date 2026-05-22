from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ApiKeyStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"
    expired = "expired"


class ApiKey(Base):
    """下游 API Key：分发给应用程序的 sk-fwr-... 凭证。不再绑定到用户，作为独立的分发实体。"""

    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_token_hash", "token_hash", unique=True),
        Index("ix_api_keys_label", "label"),
        Index("ix_api_keys_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_preview: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[ApiKeyStatus] = mapped_column(
        SAEnum(ApiKeyStatus, native_enum=False, length=32),
        default=ApiKeyStatus.active,
        nullable=False,
    )

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    unlimited_quota: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    remaining_quota_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    used_quota_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    allowed_models: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    allowed_ips: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    max_tokens_per_request: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rpm_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stream_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    total_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def is_usable(self) -> bool:
        if self.status != ApiKeyStatus.active:
            return False
        if self.expires_at is not None:
            from datetime import timezone
            if self.expires_at <= datetime.now(timezone.utc):
                return False
        if not self.unlimited_quota and self.remaining_quota_usd <= 0:
            return False
        return True
