from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
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


# SQLite 性能调优：WAL + NORMAL sync + busy_timeout + larger cache
# 在不切换到 PostgreSQL 之前，这套设置可把 SQLite 写吞吐提升 2-5×。
if _settings.is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _conn_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")           # 并发读 + 串行写
        cursor.execute("PRAGMA synchronous=NORMAL")         # 比 FULL 快很多，安全性可接受
        cursor.execute("PRAGMA busy_timeout=5000")          # 写锁等待 5s 而不是立即失败
        cursor.execute("PRAGMA cache_size=-65536")          # 64MB cache（默认 2MB）
        cursor.execute("PRAGMA temp_store=MEMORY")          # 临时表走内存
        cursor.execute("PRAGMA foreign_keys=ON")            # 启用外键约束
        cursor.close()

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
