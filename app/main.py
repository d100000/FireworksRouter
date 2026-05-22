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
from app.api import admin_auth, admin_metrics, admin_models, admin_settings
from app.api import system as system_api
from app.config import get_settings
from app.db import init_db
from app.gateway import router as gateway_router
from app.services import metrics as metrics_svc
from app.tasks.scheduler import start_scheduler, stop_scheduler
from app.utils.logger import logger, setup_logging

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    logger.info("FireworkRouter v{} starting (env={})", __version__, settings.app_env)
    await init_db()
    from app.services.settings import load_all
    await load_all()
    sched = start_scheduler()
    metrics_svc.start_workers()

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
        metrics_svc.stop_workers()
        stop_scheduler()
        _ = sched
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


app.include_router(system_api.router)
app.include_router(gateway_router.router)
app.include_router(admin_auth.router)
app.include_router(admin_api.router)
app.include_router(admin_models.router)
app.include_router(admin_settings.router)
app.include_router(admin_metrics.router)


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
