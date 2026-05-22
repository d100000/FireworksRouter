from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UpstreamKeyStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"           # 管理员手动禁用
    auto_disabled = "auto_disabled" # 余额不足 / 账户暂停 等系统自动禁用
    unhealthy = "unhealthy"         # 连续失败暂停
    testing = "testing"             # 入库时探测中


class UpstreamKey(Base):
    __tablename__ = "upstream_keys"
    __table_args__ = (
        Index("ix_upstream_keys_status", "status"),
        Index("ix_upstream_keys_account_id", "account_id"),
        Index("ix_upstream_keys_cooldown_until", "cooldown_until"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    key_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_preview: Mapped[str] = mapped_column(String(32), nullable=False)

    account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    suspend_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_state: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[UpstreamKeyStatus] = mapped_column(
        SAEnum(UpstreamKeyStatus, native_enum=False, length=32),
        default=UpstreamKeyStatus.testing,
        nullable=False,
    )

    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rpm_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    balance_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    monthly_spend_limit_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    monthly_spend_used_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    balance_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 累计统计
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_requests: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # 时间戳
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 冷却 / 退避
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    backoff_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 24h 滚动汇总（由 metrics flush 任务每分钟更新）
    success_count_24h: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count_24h: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stability_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # 最近 1h sparkline 物化字段（避免列表页 N+1 查询，由 metrics worker 每分钟刷新）
    # 形如 [{"ts": "2026-05-23T10:30:00+00:00", "success": 12, "failed": 1, "avg_ms": 850}, ...]
    recent_buckets_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recent_buckets_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 最近一次探针结果（来自 ProbeHistory 物化，避免列表页查 probe_history 表）
    last_probe_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_probe_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    auto_disable_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    @property
    def is_schedulable(self) -> bool:
        return self.enabled and self.status == UpstreamKeyStatus.active and not self.is_in_cooldown
