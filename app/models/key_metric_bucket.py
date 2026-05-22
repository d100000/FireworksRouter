from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Index,
    Integer,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class KeyMetricBucket(Base):
    """5 分钟桶聚合：每把上游 Key 在每个 5min 桶里的 success / failed / token / cost / 延迟分布。

    保留 24h，超时由后台任务清理。
    """

    __tablename__ = "key_metric_buckets"
    __table_args__ = (
        Index("ix_key_metric_buckets_pair", "upstream_key_id", "bucket_start", unique=True),
        Index("ix_key_metric_buckets_bucket_start", "bucket_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upstream_key_id: Mapped[int] = mapped_column(Integer, nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    success: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    total_latency_ms: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    max_latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    min_latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def avg_latency_ms(self) -> float:
        total = self.success + self.failed
        if total <= 0:
            return 0.0
        return self.total_latency_ms / total
