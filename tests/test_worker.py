from collections.abc import Iterator
from typing import Any

import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Job, JobStatus, User, Video
from app.services import jobs
from app.services.settings import worker_heartbeat
from app.worker import HANDLERS, JobContext, Worker
from app.youtube.rss import FEED_URL
from tests.factories import (
    BASE_TIME,
    add_channel,
    channel_id,
    feed_xml,
    rss_router,
    subscribe,
    video_id,
)


@pytest.fixture
def worker(settings: Settings, session_factory: sessionmaker[Session]) -> Iterator[Worker]:
    saved = dict(HANDLERS)
    yield Worker(settings, session_factory)
    HANDLERS.clear()
    HANDLERS.update(saved)


def test_processes_jobs_and_survives_failures(worker: Worker, db: Session) -> None:
    seen: list[dict[str, Any]] = []
    HANDLERS["ok"] = lambda _ctx, payload: seen.append(payload)

    def broken(_ctx: JobContext, _payload: dict[str, Any]) -> None:
        raise ValueError("kaputt")

    HANDLERS["broken"] = broken
    ok = jobs.enqueue(db, "ok", {"n": 1})
    bad = jobs.enqueue(db, "broken", max_attempts=1)
    unknown = jobs.enqueue(db, "unknown", max_attempts=1)

    assert worker.drain_queue() == 3
    db.expire_all()
    assert seen == [{"n": 1}]
    assert db.get(Job, ok.id).status == JobStatus.DONE.value  # type: ignore[union-attr]
    failed = db.get(Job, bad.id)
    assert failed is not None and failed.status == JobStatus.FAILED.value
    assert failed.last_error == "ValueError: kaputt"
    assert db.get(Job, unknown.id).status == JobStatus.FAILED.value  # type: ignore[union-attr]
    names = sorted(r.name for r in jobs.recent_runs(db))
    assert names == ["job:broken", "job:ok", "job:unknown"]


def test_heartbeat_and_housekeeping(worker: Worker, db: Session) -> None:
    assert worker_heartbeat(db) is None
    worker.heartbeat()
    assert worker_heartbeat(db) is not None
    worker.housekeeping()
    run = jobs.recent_runs(db)[0]
    assert run.name == "housekeeping" and run.status == "success"


def test_scheduler_has_tasks(worker: Worker) -> None:
    scheduler = worker.build_scheduler()
    assert {job.id for job in scheduler.get_jobs()} == {
        "heartbeat",
        "queue",
        "housekeeping",
        "poll_feeds",
        "enrich",
    }


def test_safe_wrapper_swallows_errors(worker: Worker) -> None:
    def boom() -> None:
        raise RuntimeError("x")

    worker._safe("boom", boom)()  # must not raise


@respx.mock
def test_fetch_channel_job(worker: Worker, db: Session, alice: User) -> None:
    channel = add_channel(db, 1)
    subscribe(db, alice.id, channel)
    feeds = {channel_id(1): feed_xml(channel_id(1), [(video_id(1), "Neu", BASE_TIME)])}
    respx.get(FEED_URL).mock(side_effect=rss_router(feeds))
    job = jobs.enqueue(db, "fetch_channel", {"channel_ids": [channel.id]})

    assert worker.drain_queue() == 1
    db.expire_all()
    assert db.get(Job, job.id).status == JobStatus.DONE.value  # type: ignore[union-attr]
    assert db.scalars(select(Video)).one().youtube_video_id == video_id(1)
