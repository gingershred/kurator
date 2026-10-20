"""Data for the admin status page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import GIT_SHA, __version__
from app.config import Settings
from app.db import utcnow
from app.migrate import head_revision
from app.models import JobRun
from app.services import jobs, users
from app.services.settings import get_global_setting, worker_heartbeat
from app.youtube import quota

WORKER_ALIVE_WITHIN = timedelta(minutes=3)


@dataclass(frozen=True)
class SystemStatus:
    version: str
    git_sha: str
    db_revision: str | None
    head_revision: str
    db_size_bytes: int
    user_count: int
    auth_mode: str
    youtube_api_key_set: bool
    quota_used: int
    quota_search_used: int
    quota_budget: int
    worker_heartbeat: datetime | None
    worker_alive: bool
    queue: dict[str, int]
    recent_runs: list[JobRun]

    @property
    def db_up_to_date(self) -> bool:
        return self.db_revision == self.head_revision


def _db_revision(db: Session) -> str | None:
    try:
        return db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except SQLAlchemyError:
        return None


def _db_size(settings: Settings) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        path = settings.db_path.with_name(settings.db_path.name + suffix)
        if path.exists():
            total += path.stat().st_size
    return total


def collect(db: Session, settings: Settings, now: datetime | None = None) -> SystemStatus:
    now = now or utcnow()
    heartbeat = worker_heartbeat(db)
    return SystemStatus(
        version=__version__,
        git_sha=GIT_SHA,
        db_revision=_db_revision(db),
        head_revision=head_revision(),
        db_size_bytes=_db_size(settings),
        user_count=users.count_users(db),
        auth_mode=settings.auth_mode,
        youtube_api_key_set=settings.youtube_api_key is not None,
        quota_used=quota.used_today(db, now),
        quota_search_used=quota.search_used_today(db, now),
        quota_budget=int(get_global_setting(db, settings, "quota_daily_budget")),
        worker_heartbeat=heartbeat,
        worker_alive=heartbeat is not None and now - heartbeat <= WORKER_ALIVE_WITHIN,
        queue=jobs.queue_counts(db),
        recent_runs=jobs.recent_runs(db, limit=20),
    )
