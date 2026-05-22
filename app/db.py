from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

_engine_kwargs: dict = {"echo": False}
if not _settings.is_sqlite:
    _engine_kwargs.update(pool_pre_ping=True, pool_size=20, max_overflow=40)

engine = create_async_engine(_settings.database_url, **_engine_kwargs)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """启动时把数据库升级到最新版本（alembic upgrade head）。"""
    # 触发所有模型注册到 Base.metadata
    from app import models  # noqa: F401

    import asyncio
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    def _upgrade() -> None:
        cfg_path = Path(__file__).resolve().parent.parent / "alembic.ini"
        if not cfg_path.exists():
            # 兜底（如打包后没带 alembic.ini）：用 metadata.create_all
            return
        cfg = Config(str(cfg_path))
        cfg.set_main_option("sqlalchemy.url", _settings.database_url)
        command.upgrade(cfg, "head")

    await asyncio.to_thread(_upgrade)
