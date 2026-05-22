from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.services.balance import run_probe_round
from app.utils.logger import logger

settings = get_settings()

_scheduler: AsyncIOScheduler | None = None


async def _probe_job() -> None:
    try:
        await run_probe_round()
    except Exception as e:  # noqa: BLE001
        logger.exception("probe job failed: {}", e)


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(
        _probe_job,
        trigger=IntervalTrigger(minutes=settings.probe_interval_minutes),
        id="probe_round",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    sched.start()
    _scheduler = sched
    logger.info(
        "scheduler started: probe every {} minute(s), concurrency={}",
        settings.probe_interval_minutes,
        settings.probe_concurrency,
    )
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
