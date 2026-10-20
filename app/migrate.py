"""Run Alembic migrations on startup, with a database backup before every schema change."""

from __future__ import annotations

import fcntl
import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from app.config import Settings
from app.db import create_db_engine

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUPS_TO_KEEP = 10
BACKUP_PREFIX = "kurator-"


@dataclass(frozen=True)
class MigrationResult:
    previous_revision: str | None
    head_revision: str
    backup: Path | None

    @property
    def upgraded(self) -> bool:
        return self.previous_revision != self.head_revision


def alembic_config() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    return cfg


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:  # pragma: no cover - there is always at least one migration
        raise RuntimeError("no migrations found")
    return head


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def backup_database(settings: Settings, label: str) -> Path:
    """Create a consistent copy of the database (safe while in WAL mode) and rotate old ones."""
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = settings.backups_dir / f"{BACKUP_PREFIX}{stamp}-{label}.db"
    src = sqlite3.connect(settings.db_path)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    prune_backups(settings.backups_dir)
    log.info("database backup written to %s", target)
    return target


def prune_backups(backups_dir: Path, keep: int = BACKUPS_TO_KEEP) -> list[Path]:
    """Delete all but the newest `keep` backups. File names sort chronologically."""
    backups = sorted(backups_dir.glob(f"{BACKUP_PREFIX}*.db"))
    removed = backups[:-keep] if keep > 0 else backups
    for path in removed:
        path.unlink(missing_ok=True)
    return removed


@contextmanager
def _migration_lock(settings: Settings) -> Iterator[None]:
    """Serialise migrations between processes (web and worker share the data volume)."""
    lock_path = settings.data_dir / ".migrate.lock"
    with lock_path.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def run_migrations(settings: Settings) -> MigrationResult:
    """Back up the database if a schema change is pending, then upgrade to head."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    head = head_revision()
    with _migration_lock(settings):
        engine = create_db_engine(settings.database_url)
        try:
            previous = current_revision(engine)
            backup: Path | None = None
            if previous == head:
                return MigrationResult(previous, head, None)
            db_has_data = settings.db_path.exists() and settings.db_path.stat().st_size > 0
            if db_has_data and previous is not None:
                backup = backup_database(settings, f"before-{head}-from-{previous}")
            log.info("migrating database from %s to %s", previous or "empty", head)
            cfg = alembic_config()
            with engine.begin() as conn:
                cfg.attributes["connection"] = conn
                command.upgrade(cfg, "head")
            return MigrationResult(previous, head, backup)
        finally:
            engine.dispose()


def wait_for_head(settings: Settings, timeout: float = 120.0, interval: float = 2.0) -> None:
    """Block until the database schema is at head (used by the worker)."""
    head = head_revision()
    deadline = time.monotonic() + timeout
    engine = create_db_engine(settings.database_url)
    try:
        while True:
            if settings.db_path.exists() and current_revision(engine) == head:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"database did not reach revision {head} in {timeout}s")
            time.sleep(interval)
    finally:
        engine.dispose()
