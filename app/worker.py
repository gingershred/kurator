"""Background worker: APScheduler for periodic tasks plus a poller for the DB job queue.

Runs as its own process (`python -m app.cli worker`) from the same image as the web app.
A failing job or task is logged and recorded, but never stops the worker.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from types import FrameType
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import utcnow
from app.models import Job
from app.playback import download_job, downloads
from app.services import ingest, jobs
from app.services.settings import get_global_setting, record_worker_heartbeat
from app.youtube.api import YouTubeApi
from app.youtube.thumbs import prune_orphans

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobContext:
    db: Session
    settings: Settings
    session_factory: sessionmaker[Session]
    job: Job | None = None


JobHandler = Callable[[JobContext, dict[str, Any]], None]

# Job type -> handler. Later phases register downloads etc. here.
HANDLERS: dict[str, JobHandler] = {}

QUEUE_POLL_SECONDS = 5
HEARTBEAT_SECONDS = 60
HOUSEKEEPING_HOURS = 6
ENRICH_MINUTES = 10
DOWNLOAD_POLL_SECONDS = 5
CLEANUP_MINUTES = 60
MAX_DOWNLOAD_CONCURRENCY = 3
# Job types handled by their own lane, so long downloads never block feed jobs.
DOWNLOAD_TYPES = (downloads.DOWNLOAD_JOB,)

# Feed polling, enrichment and fetch jobs run in different scheduler threads; running them one
# at a time avoids duplicate inserts and duplicate (quota-costing) API calls.
FETCH_LOCK = threading.Lock()


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
        self._download_threads: list[threading.Thread] = []
        self._download_lock = threading.Lock()

    def process_one(self, download_lane: bool = False) -> bool:
        """Run the next due job of a lane, if any. Returns True if a job was processed."""
        with self.session_factory() as db:
            if download_lane:
                job = jobs.claim_next(db, self.worker_id, types=DOWNLOAD_TYPES)
            else:
                job = jobs.claim_next(db, self.worker_id, exclude=DOWNLOAD_TYPES)
            if job is None:
                return False
            handler = HANDLERS.get(job.type)
            try:
                with jobs.track_run(self.session_factory, f"job:{job.type}", job.id):
                    if handler is None:
                        raise LookupError(f"no handler registered for job type {job.type!r}")
                    handler(JobContext(db, self.settings, self.session_factory, job), job.payload)
            except jobs.Postpone as postponed:
                log.info("job %s (%s) postponed: %s", job.id, job.type, postponed)
                db.rollback()
                jobs.postpone(db, job, postponed.seconds)
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

    def download_concurrency(self) -> int:
        with self.session_factory() as db:
            value = int(get_global_setting(db, self.settings, "download_concurrency"))
        return max(1, min(MAX_DOWNLOAD_CONCURRENCY, value))

    def download_tick(self) -> int:
        """Start download threads up to the configured concurrency. Returns how many started."""
        started = 0
        with self._download_lock:
            self._download_threads = [t for t in self._download_threads if t.is_alive()]
            free = self.download_concurrency() - len(self._download_threads)
            for _ in range(max(0, free)):
                thread = threading.Thread(
                    target=self.process_one, kwargs={"download_lane": True}, daemon=True
                )
                thread.start()
                self._download_threads.append(thread)
                started += 1
        return started

    def cleanup_downloads(self) -> None:
        with self.session_factory() as db:
            result = downloads.cleanup(db, self.settings)
        if result.orphaned or result.expired or result.over_limit:
            with jobs.track_run(self.session_factory, "cleanup_downloads") as run:
                run.detail = asdict(result)

    def heartbeat(self) -> None:
        with self.session_factory() as db:
            record_worker_heartbeat(db, utcnow())

    def housekeeping(self) -> None:
        with jobs.track_run(self.session_factory, "housekeeping") as run:
            with self.session_factory() as db:
                requeued = jobs.requeue_stale(db)
                pruned = jobs.prune_runs(db)
                thumbs_removed = prune_orphans(db, self.settings)
            run.detail = {"requeued": requeued, "pruned": pruned, "thumbs_removed": thumbs_removed}

    def poll_feeds(self) -> None:
        with FETCH_LOCK, jobs.track_run(self.session_factory, "poll_feeds") as run:
            polled, enriched, thumbs = asyncio.run(
                ingest.poll_and_enrich(self.session_factory, self.settings)
            )
            run.detail = {
                "poll": asdict(polled),
                "enrich": asdict(enriched),
                "thumbs": asdict(thumbs),
            }

    def enrich(self) -> None:
        """Re-check live/upcoming videos and catch up on enrichment between polls."""
        if self.settings.youtube_api_key is None:
            return

        async def run_enrich() -> ingest.EnrichResult:
            async with ingest.http_client() as client:
                api = YouTubeApi(self.settings, self.session_factory, client)
                return await ingest.enrich_pending(self.session_factory, api, self.settings)

        with FETCH_LOCK:
            result = asyncio.run(run_enrich())
        if result.requested:
            with jobs.track_run(self.session_factory, "enrich") as run:
                run.detail = asdict(result)

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
            self._safe("poll_feeds", self.poll_feeds),
            "interval",
            minutes=self.settings.feed_poll_minutes,
            next_run_time=utcnow(),
            id="poll_feeds",
            name="poll_feeds",
            **common,
        )
        scheduler.add_job(
            self._safe("enrich", self.enrich),
            "interval",
            minutes=ENRICH_MINUTES,
            id="enrich",
            name="enrich",
            **common,
        )
        scheduler.add_job(
            self._safe("downloads", self.download_tick),
            "interval",
            seconds=DOWNLOAD_POLL_SECONDS,
            id="downloads",
            name="downloads",
            **common,
        )
        scheduler.add_job(
            self._safe("cleanup_downloads", self.cleanup_downloads),
            "interval",
            minutes=CLEANUP_MINUTES,
            id="cleanup_downloads",
            name="cleanup_downloads",
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
        with self.session_factory() as db:
            recovered = download_job.recover_interrupted(db)
            # Their queue jobs were left "running" as well.
            jobs.requeue_stale(db, utcnow() + jobs.STALE_AFTER)
        if recovered:
            log.info("re-queued %d interrupted download(s)", recovered)
        log.info("worker %s started", self.worker_id)
        scheduler.start()
        log.info("worker stopped")


@register("fetch_channel")
def fetch_channel(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Fetch one channel right away (e.g. after it was added)."""
    channel_ids = [int(cid) for cid in payload.get("channel_ids", [])]
    with FETCH_LOCK:
        polled, _enriched, _thumbs = asyncio.run(
            ingest.poll_and_enrich(ctx.session_factory, ctx.settings, channel_ids)
        )
    if polled.failed and not polled.ok:
        raise RuntimeError(f"fetching {polled.failed} channel(s) failed")


@register(downloads.DOWNLOAD_JOB)
def download(ctx: JobContext, payload: dict[str, Any]) -> None:
    download_job.run(
        ctx.db, ctx.settings, ctx.session_factory, ctx.job, int(payload["download_id"])
    )
