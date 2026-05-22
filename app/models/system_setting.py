from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SystemSetting(Base):
    """简单 KV 配置表；value 用 JSON 列以兼容 str / number / list / dict。"""

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    value: Mapped[object] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
