from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.db import utcnow
from app.models import JobRun, JobStatus, RunStatus
from app.services import jobs


def test_claim_order_and_due_time(db: Session) -> None:
    now = utcnow()
    low = jobs.enqueue(db, "a", priority=0, run_after=now - timedelta(minutes=5))
    high = jobs.enqueue(db, "b", priority=10, run_after=now - timedelta(minutes=1))
    jobs.enqueue(db, "later", priority=99, run_after=now + timedelta(hours=1))

    first = jobs.claim_next(db, "w1", now)
    second = jobs.claim_next(db, "w1", now)
    assert first is not None and first.id == high.id
    assert second is not None and second.id == low.id
    assert jobs.claim_next(db, "w1", now) is None
    assert first.status == JobStatus.RUNNING.value
    assert first.attempts == 1 and first.locked_by == "w1"


def test_job_claimed_only_once(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        jobs.enqueue(db, "x")
    with session_factory() as a, session_factory() as b:
        assert jobs.claim_next(a, "a") is not None
        assert jobs.claim_next(b, "b") is None


def test_fail_retries_with_backoff_then_fails(db: Session) -> None:
    now = utcnow()
    job = jobs.enqueue(db, "flaky", max_attempts=2, run_after=now)
    claimed = jobs.claim_next(db, "w", now)
    assert claimed is not None
    jobs.fail(db, claimed, "boom", now)
    assert claimed.status == JobStatus.QUEUED.value
    assert claimed.run_after == now + timedelta(seconds=jobs.RETRY_BASE_SECONDS)
    assert jobs.claim_next(db, "w", now) is None  # not due yet

    later = now + timedelta(hours=1)
    claimed = jobs.claim_next(db, "w", later)
    assert claimed is not None and claimed.id == job.id
    jobs.fail(db, claimed, "boom again", later)
    assert claimed.status == JobStatus.FAILED.value
    assert claimed.last_error == "boom again"


def test_requeue_stale(db: Session) -> None:
    now = utcnow()
    jobs.enqueue(db, "x", run_after=now - timedelta(hours=4))
    job = jobs.claim_next(db, "dead-worker", now - timedelta(hours=3))
    assert job is not None
    assert jobs.requeue_stale(db, now) == 1
    db.refresh(job)
    assert job.status == JobStatus.QUEUED.value


def test_track_run_success_and_failure(db: Session, session_factory: sessionmaker[Session]) -> None:
    with jobs.track_run(session_factory, "ok") as run:
        run.detail = {"n": 1}
    with pytest.raises(RuntimeError), jobs.track_run(session_factory, "broken"):
        raise RuntimeError("kaputt")

    runs = {r.name: r for r in jobs.recent_runs(db)}
    assert runs["ok"].status == RunStatus.SUCCESS.value
    assert runs["ok"].detail == {"n": 1}
    assert runs["ok"].finished_at is not None
    assert runs["broken"].status == RunStatus.FAILED.value
    assert runs["broken"].error is not None and "kaputt" in runs["broken"].error


def test_prune_runs_and_counts(db: Session) -> None:
    old = utcnow() - timedelta(days=40)
    db.add(JobRun(name="old", status="success", started_at=old))
    db.add(JobRun(name="new", status="success"))
    db.commit()
    jobs.enqueue(db, "waiting")
    assert jobs.prune_runs(db) == 1
    assert [r.name for r in jobs.recent_runs(db)] == ["new"]
    assert jobs.queue_counts(db) == {"queued": 1, "running": 0, "done": 0, "failed": 0}
