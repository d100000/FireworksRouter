from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import admin as admin_api
from app.api import admin_auth, admin_logs, admin_metrics, admin_models, admin_price_catalog, admin_settings
from app.api import system as system_api
from app.config import get_settings
from app.db import init_db
from app.gateway import anthropic as anthropic_router
from app.gateway import router as gateway_router
from app.gateway.proxy import close_shared_client, init_shared_client
from app.services import metrics as metrics_svc
from app.tasks.scheduler import start_scheduler, stop_scheduler
from app.utils.logger import logger, setup_logging, start_db_sink, stop_db_sink

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    logger.info("FireworkRouter v{} starting (env={})", __version__, settings.app_env)
    await init_db()
    from app.services.settings import load_all
    await load_all()
    # 首次启动种子价格表（从 hardcoded KNOWN_PRICES）
    from app.db import session_scope
    from app.services import price_catalog as _pc
    async with session_scope() as _s:
        await _pc.seed_initial(_s)
    sched = start_scheduler()
    metrics_svc.start_workers()
    init_shared_client()
    # 启动 DB sink worker（之前 setup_logging 已注册 sink，但 worker 未起，
    # 早期 INFO/WARNING 会沉默丢弃；从这里起入 DB）
    await start_db_sink()

    if settings.probe_on_startup:
        async def _initial_probe() -> None:
            try:
                from app.services.balance import run_probe_round
                logger.info("running initial probe round...")
                await run_probe_round()
            except Exception as e:  # noqa: BLE001
                logger.exception("initial probe round failed: {}", e)
        asyncio.create_task(_initial_probe())

    try:
        yield
    finally:
        await close_shared_client()
        metrics_svc.stop_workers()
        stop_scheduler()
        _ = sched
        # 在 logger 关闭前 flush 一次 DB sink，再退出
        await stop_db_sink()
        logger.info("FireworkRouter shutdown complete")


app = FastAPI(
    title="FireworkRouter",
    version=__version__,
    description="单管理端 Fireworks.ai 中转分发：多 Key 池、调度策略、错误码退避、稳定性监控",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# 把 APIError 的 detail 字段直接作为响应体（不被 FastAPI 包成 {"detail": ...}）
from fastapi import Request as _Req
from fastapi.responses import JSONResponse as _JR
from app.api.deps import APIError as _APIError


@app.exception_handler(_APIError)
async def _api_error_handler(_request: _Req, exc: _APIError) -> _JR:
    return _JR(status_code=exc.status_code, content=exc.api_error_payload, headers=exc.headers or {})


app.include_router(system_api.router)

# OpenAI 兼容网关：主路径 /v1/* 以及两个常见路径别名 /openai/v1/* 和 /api/v1/*
# 让用错 SDK 配置（带前缀的 base_url）的客户端也能 work
app.include_router(gateway_router.router)
app.include_router(gateway_router.router, prefix="/openai", include_in_schema=False)
app.include_router(gateway_router.router, prefix="/api", include_in_schema=False)

# Anthropic Claude 协议兼容（Claude Code / Anthropic SDK）
app.include_router(anthropic_router.router)

app.include_router(admin_auth.router)
app.include_router(admin_api.router)
app.include_router(admin_models.router)
app.include_router(admin_price_catalog.router)
app.include_router(admin_settings.router)
app.include_router(admin_metrics.router)
app.include_router(admin_logs.router)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", tags=["meta"])
async def readyz() -> dict[str, str]:
    return {"status": "ready"}


# ============== 静态前端 SPA ==============
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"

if FRONTEND_INDEX.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST / "assets"),
        name="frontend-assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_INDEX)
else:

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "service": "FireworkRouter",
                "version": __version__,
                "openai_compatible_base_url": "/v1",
                "admin_base_url": "/admin",
                "docs_url": "/docs",
                "hint": "frontend/dist not found — run `cd frontend && npm run build` to enable web UI at /",
            }
        )
