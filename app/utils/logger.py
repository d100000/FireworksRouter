from __future__ import annotations

import sys

from loguru import logger

from app.config import get_settings


def setup_logging() -> None:
    settings = get_settings()
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.log_level.upper(),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>{level:<7}</level> "
            "<cyan>{name}:{function}:{line}</cyan> "
            "{message}"
        ),
        backtrace=False,
        diagnose=False,
        enqueue=True,
    )


__all__ = ["logger", "setup_logging"]
