"""SystemLog 模型：把 loguru 应用日志持久化到 DB，方便 UI 查询/清理。

只入库 WARNING+（默认，可由 `system_log_min_level` 调整）。
INFO/DEBUG 仍只去 stdout，避免量大拖慢 DB。

异步 sink 见 app/utils/logger.py 中的 _db_sink。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SystemLog(Base):
    __tablename__ = "system_logs"
    __table_args__ = (
        Index("ix_system_logs_timestamp_desc", "timestamp"),
        Index("ix_system_logs_level_timestamp", "level", "timestamp"),
        Index("ix_system_logs_request_id", "request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 日志原本时间（loguru.record["time"]），不一定等于 created_at
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )

    # DEBUG / INFO / WARNING / ERROR / CRITICAL（统一大写）
    level: Mapped[str] = mapped_column(String(16), nullable=False)

    # 来源 — app.services.models 这种 dotted path
    module: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    function: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    line: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 渲染后的消息文本
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # 关联到某次 /v1/* 调用（forward 链路里 request_id 形如 fwr-xxxx），可空
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 异常 traceback / 结构化上下文 — JSON 文本（SQLite/PG 都用 TEXT 兜底，避免类型差异）
    extra: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
