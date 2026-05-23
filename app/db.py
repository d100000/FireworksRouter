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
    # 连接池调优：
    #   pool_size=10            常驻 10 个 — 满足正常 API 流量
    #   max_overflow=20         峰值时再开 20 个临时连接（如全量探针 / 批量导入）
    #                           总上限 = 30，给 PG 默认 max_connections=100 留出 70 个
    #                           给其它客户端（监控、psql、CI 工具）。
    #   pool_pre_ping=True      取连接时 ping 一下，避免拿到 stale conn
    #   pool_recycle=1800       连接活到 30 分钟强制回收 — 防止 PG 服务端断 idle 连接
    #                           （PG idle_session_timeout / 防火墙 NAT 超时 etc.）
    #   pool_timeout=30         拿不到连接最多等 30s 就抛 TimeoutError —— 不要无限挂死
    #
    # 历史 bug：之前 pool_size=20 + max_overflow=40 = 上限 60，再叠加 probe HTTP
    # 长时间占用连接的 bug，单波探针就能把池耗光（见 #81 修复）。
    _engine_kwargs.update(
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
        pool_timeout=30,
    )

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
    """启动时把数据库升级到最新版本（alembic upgrade head）。

    重要：docker compose 模式下 PG 容器的 `pg_isready` 通过后实际可能仍未完全准备好
    接受 SQL 连接（尤其首次 initdb 跑完）。alembic 第一次连接可能 OperationalError，
    所以加最多 30 次重试，每次间隔 2 秒。
    """
    # 触发所有模型注册到 Base.metadata
    from app import models  # noqa: F401

    import asyncio
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    def _upgrade() -> None:
        cfg_path = Path(__file__).resolve().parent.parent / "alembic.ini"
        if not cfg_path.exists():
            return
        cfg = Config(str(cfg_path))
        cfg.set_main_option("sqlalchemy.url", _settings.database_url)
        command.upgrade(cfg, "head")

    last_err: Exception | None = None
    for attempt in range(30):
        try:
            await asyncio.to_thread(_upgrade)
            if attempt > 0:
                from app.utils.logger import logger
                logger.info("alembic upgrade head 在第 {} 次重试后成功", attempt + 1)
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            from app.utils.logger import logger
            logger.warning(
                "alembic upgrade attempt {}/30 failed: {} — retry in 2s",
                attempt + 1, type(e).__name__,
            )
            await asyncio.sleep(2)

    raise RuntimeError(f"alembic upgrade 在 30 次重试后仍失败：{last_err}")
