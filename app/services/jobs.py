"""DB-backed job queue and run history."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db import utcnow
from app.models import Job, JobRun, JobStatus, RunStatus

log = logging.getLogger(__name__)

RETRY_BASE_SECONDS = 60
STALE_AFTER = timedelta(hours=2)
MAX_ERROR_LENGTH = 4000


def enqueue(
    db: Session,
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    priority: int = 0,
    run_after: datetime | None = None,
    max_attempts: int = 3,
    created_by: int | None = None,
) -> Job:
    job = Job(
        type=job_type,
        payload=payload or {},
        priority=priority,
        run_after=run_after or utcnow(),
        max_attempts=max_attempts,
        created_by=created_by,
    )
    db.add(job)
    db.commit()
    return job


class Postpone(Exception):
    """Raised by a handler to put its job back into the queue without using up an attempt."""

    def __init__(self, seconds: int, reason: str = "") -> None:
        super().__init__(reason or f"postponed for {seconds}s")
        self.seconds = seconds


def claim_next(
    db: Session,
    worker_id: str,
    now: datetime | None = None,
    *,
    types: Collection[str] | None = None,
    exclude: Collection[str] | None = None,
) -> Job | None:
    """Atomically mark the next due job as running and return it.

    `types` restricts to these job types, `exclude` skips them (separate worker lanes).
    """
    now = now or utcnow()
    query = select(Job.id).where(Job.status == JobStatus.QUEUED.value, Job.run_after <= now)
    if types is not None:
        query = query.where(Job.type.in_(list(types)))
    if exclude:
        query = query.where(Job.type.not_in(list(exclude)))
    next_id = query.order_by(Job.priority.desc(), Job.run_after, Job.id).limit(1).scalar_subquery()
    claimed = db.execute(
        update(Job)
        .where(Job.id == next_id, Job.status == JobStatus.QUEUED.value)
        .values(
            status=JobStatus.RUNNING.value,
            locked_by=worker_id,
            locked_at=now,
            attempts=Job.attempts + 1,
            updated_at=now,
        )
        .returning(Job.id)
    ).scalar_one_or_none()
    db.commit()
    if claimed is None:
        return None
    return db.get(Job, claimed, populate_existing=True)


def postpone(db: Session, job: Job, seconds: int, now: datetime | None = None) -> None:
    """Back into the queue without counting the attempt."""
    job.status = JobStatus.QUEUED.value
    job.attempts = max(0, job.attempts - 1)
    job.run_after = (now or utcnow()) + timedelta(seconds=seconds)
    job.locked_by = None
    job.locked_at = None
    db.commit()


def complete(db: Session, job: Job) -> None:
    job.status = JobStatus.DONE.value
    job.locked_by = None
    job.locked_at = None
    job.last_error = None
    db.commit()


def fail(db: Session, job: Job, error: str, now: datetime | None = None) -> None:
    """Record an error; retry with exponential backoff until max_attempts is reached."""
    now = now or utcnow()
    job.last_error = error[-MAX_ERROR_LENGTH:]
    job.locked_by = None
    job.locked_at = None
    if job.attempts < job.max_attempts:
        job.status = JobStatus.QUEUED.value
        job.run_after = now + timedelta(seconds=RETRY_BASE_SECONDS * 2 ** (job.attempts - 1))
    else:
        job.status = JobStatus.FAILED.value
    db.commit()


def requeue_stale(db: Session, now: datetime | None = None) -> int:
    """Put jobs back into the queue whose worker died while running them."""
    now = now or utcnow()
    result = db.execute(
        update(Job)
        .where(Job.status == JobStatus.RUNNING.value, Job.locked_at < now - STALE_AFTER)
        .values(status=JobStatus.QUEUED.value, locked_by=None, locked_at=None, updated_at=now)
    )
    db.commit()
    return int(result.rowcount)  # type: ignore[attr-defined]


def queue_counts(db: Session) -> dict[str, int]:
    rows = db.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    counts = {status.value: 0 for status in JobStatus}
    counts.update({status: count for status, count in rows})
    return counts


@contextmanager
def track_run(
    factory: sessionmaker[Session], name: str, job_id: int | None = None
) -> Iterator[JobRun]:
    """Record a run in `job_run`. Exceptions are recorded and re-raised."""
    with factory() as db:
        run = JobRun(name=name, job_id=job_id, status=RunStatus.RUNNING.value)
        db.add(run)
        db.commit()
        try:
            yield run
        except Exception as exc:
            run.status = RunStatus.FAILED.value
            run.error = (f"{type(exc).__name__}: {exc}\n" + traceback.format_exc())[
                -MAX_ERROR_LENGTH:
            ]
            raise
        else:
            run.status = RunStatus.SUCCESS.value
        finally:
            run.finished_at = utcnow()
            db.merge(run)
            db.commit()


def recent_runs(db: Session, limit: int = 20) -> list[JobRun]:
    return list(db.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)))


def prune_runs(db: Session, keep_days: int = 30, now: datetime | None = None) -> int:
    cutoff = (now or utcnow()) - timedelta(days=keep_days)
    result = db.execute(delete(JobRun).where(JobRun.started_at < cutoff))
    finished = db.execute(
        delete(Job).where(
            Job.status.in_([JobStatus.DONE.value, JobStatus.FAILED.value]),
            Job.updated_at < cutoff,
        )
    )
    db.commit()
    return int(result.rowcount) + int(finished.rowcount)  # type: ignore[attr-defined]
