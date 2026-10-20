import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from app import migrate
from app.config import Settings
from app.db import create_db_engine


def test_fresh_database_migrates_without_backup(settings: Settings) -> None:
    result = migrate.run_migrations(settings)
    assert result.previous_revision is None
    assert result.head_revision == migrate.head_revision()
    assert result.backup is None
    assert settings.db_path.exists()


def test_up_to_date_database_is_left_alone(settings: Settings) -> None:
    migrate.run_migrations(settings)
    result = migrate.run_migrations(settings)
    assert not result.upgraded
    assert result.backup is None
    assert not settings.backups_dir.exists()


def test_pending_migration_creates_backup_with_data(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate.run_migrations(settings)
    engine = create_db_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO "user" (username, role, created_at, updated_at) '
                "VALUES ('alice', 'admin', '2026-01-01', '2026-01-01')"
            )
        )
    engine.dispose()

    # Pretend the database is at an older revision so an upgrade is pending.
    monkeypatch.setattr(migrate, "current_revision", lambda _engine: "0000")
    monkeypatch.setattr("alembic.command.upgrade", lambda _cfg, _rev: None)

    result = migrate.run_migrations(settings)

    assert result.backup is not None and result.backup.exists()
    assert "from-0000" in result.backup.name
    with sqlite3.connect(result.backup) as conn:
        assert conn.execute('SELECT username FROM "user"').fetchall() == [("alice",)]


def test_backup_rotation_keeps_newest_ten(tmp_path: Path) -> None:
    for i in range(13):
        (tmp_path / f"kurator-2026010{i // 10}T00000{i % 10}Z-x.db").write_bytes(b"")
    (tmp_path / "unrelated.db").write_bytes(b"")

    removed = migrate.prune_backups(tmp_path)

    remaining = sorted(p.name for p in tmp_path.glob("kurator-*.db"))
    assert len(remaining) == 10
    assert len(removed) == 3
    assert all(p.name < remaining[0] for p in removed)
    assert (tmp_path / "unrelated.db").exists()


def test_backup_database_rotates(settings: Settings) -> None:
    migrate.run_migrations(settings)
    for i in range(12):
        migrate.backup_database(settings, f"t{i}")
    assert len(list(settings.backups_dir.glob("kurator-*.db"))) == 10


def test_wait_for_head(settings: Settings) -> None:
    with pytest.raises(TimeoutError):
        migrate.wait_for_head(settings, timeout=0.01, interval=0.01)
    migrate.run_migrations(settings)
    migrate.wait_for_head(settings, timeout=1)
