"""Logging setup shared by all entry points. Secrets are never logged."""

from __future__ import annotations

import logging

from app.config import Settings

QUIET_LOGGERS = ("alembic.runtime.plugins", "apscheduler.scheduler", "apscheduler.executors")


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    if settings.log_level != "DEBUG":
        for name in QUIET_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
