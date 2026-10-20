"""Background worker: APScheduler for periodic tasks plus a poller for the DB job queue.

Runs as its own process (`python -m app.cli worker`) from the same image as the web app.
A failing job or task is logged and recorded, but never stops the worker.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
from collections.abc import Callable
from types import FrameType
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import utcnow
from app.services import jobs
from app.services.settings import record_worker_heartbeat

log = logging.getLogger(__name__)

JobHandler = Callable[[Session, dict[str, Any]], None]

# Job type -> handler. Later phases register downloads etc. here.
HANDLERS: dict[str, JobHandler] = {}

QUEUE_POLL_SECONDS = 5
HEARTBEAT_SECONDS = 60
HOUSEKEEPING_HOURS = 6


def register(job_type: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(func: JobHandler) -> JobHandler:
        HANDLERS[job_type] = func
        return func

    return decorator


class Worker:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session]) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"

    def process_one(self) -> bool:
        """Run the next due job, if any. Returns True if a job was processed."""
        with self.session_factory() as db:
            job = jobs.claim_next(db, self.worker_id)
            if job is None:
                return False
            handler = HANDLERS.get(job.type)
            try:
                with jobs.track_run(self.session_factory, f"job:{job.type}", job.id):
                    if handler is None:
                        raise LookupError(f"no handler registered for job type {job.type!r}")
                    handler(db, job.payload)
            except Exception as exc:
                log.exception("job %s (%s) failed", job.id, job.type)
                db.rollback()
                jobs.fail(db, job, f"{type(exc).__name__}: {exc}")
            else:
                jobs.complete(db, job)
            return True

    def drain_queue(self, limit: int = 100) -> int:
        processed = 0
        while processed < limit and self.process_one():
            processed += 1
        return processed

    def heartbeat(self) -> None:
        with self.session_factory() as db:
            record_worker_heartbeat(db, utcnow())

    def housekeeping(self) -> None:
        with jobs.track_run(self.session_factory, "housekeeping") as run:
            with self.session_factory() as db:
                requeued = jobs.requeue_stale(db)
                pruned = jobs.prune_runs(db)
            run.detail = {"requeued": requeued, "pruned": pruned}

    def _safe(self, name: str, func: Callable[[], object]) -> Callable[[], None]:
        def wrapper() -> None:
            try:
                func()
            except Exception:
                log.exception("scheduled task %s failed", name)

        return wrapper

    def build_scheduler(self) -> BlockingScheduler:
        scheduler = BlockingScheduler(timezone=self.settings.timezone)
        common: dict[str, Any] = {"max_instances": 1, "coalesce": True}
        scheduler.add_job(
            self._safe("heartbeat", self.heartbeat),
            "interval",
            seconds=HEARTBEAT_SECONDS,
            next_run_time=utcnow(),
            id="heartbeat",
            name="heartbeat",
            **common,
        )
        scheduler.add_job(
            self._safe("queue", self.drain_queue),
            "interval",
            seconds=QUEUE_POLL_SECONDS,
            id="queue",
            name="queue",
            **common,
        )
        scheduler.add_job(
            self._safe("housekeeping", self.housekeeping),
            "interval",
            hours=HOUSEKEEPING_HOURS,
            next_run_time=utcnow(),
            id="housekeeping",
            name="housekeeping",
            **common,
        )
        return scheduler

    def run_forever(self) -> None:
        scheduler = self.build_scheduler()

        def stop(signum: int, _frame: FrameType | None) -> None:
            log.info("received signal %s, shutting down", signum)
            scheduler.shutdown(wait=False)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        log.info("worker %s started", self.worker_id)
        scheduler.start()
        log.info("worker stopped")
