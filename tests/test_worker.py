from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Job, JobStatus
from app.services import jobs
from app.services.settings import worker_heartbeat
from app.worker import HANDLERS, Worker


@pytest.fixture
def worker(settings: Settings, session_factory: sessionmaker[Session]) -> Iterator[Worker]:
    saved = dict(HANDLERS)
    yield Worker(settings, session_factory)
    HANDLERS.clear()
    HANDLERS.update(saved)


def test_processes_jobs_and_survives_failures(worker: Worker, db: Session) -> None:
    seen: list[dict[str, Any]] = []
    HANDLERS["ok"] = lambda _db, payload: seen.append(payload)

    def broken(_db: Session, _payload: dict[str, Any]) -> None:
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
    assert {job.id for job in scheduler.get_jobs()} == {"heartbeat", "queue", "housekeeping"}


def test_safe_wrapper_swallows_errors(worker: Worker) -> None:
    def boom() -> None:
        raise RuntimeError("x")

    worker._safe("boom", boom)()  # must not raise
