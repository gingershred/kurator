from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.models import GlobalSetting, Job, QuotaLedger, User, UserSetting


def test_migrations_match_models(engine: Engine) -> None:
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(ctx, Base.metadata) == []


def test_sqlite_pragmas(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_datetimes_are_utc_aware(db: Session) -> None:
    db.add(User(username="alice", role="user"))
    db.commit()
    db.expire_all()
    user = db.scalars(select(User)).one()
    assert user.created_at.tzinfo is UTC


def test_naive_datetime_rejected(db: Session) -> None:
    db.add(User(username="alice", role="user", last_login_at=datetime(2026, 1, 1)))
    with pytest.raises(Exception, match="naive"):
        db.commit()


def test_username_unique_case_insensitive(db: Session) -> None:
    db.add(User(username="Alice", role="user"))
    db.commit()
    db.add(User(username="alice", role="user"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_role_check_constraint(db: Session) -> None:
    db.add(User(username="mallory", role="root"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_user_cascades_and_nulls(db: Session) -> None:
    user = User(username="alice", role="admin")
    db.add(user)
    db.flush()
    db.add_all(
        [
            UserSetting(user_id=user.id, key="feed_sort", value="newest"),
            GlobalSetting(key="downloads_enabled", value=True, updated_by=user.id),
            Job(type="noop", created_by=user.id),
            QuotaLedger(
                quota_day=datetime.now(UTC).date(), method="videos.list", units=1, user_id=user.id
            ),
        ]
    )
    db.commit()

    db.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": user.id})
    db.commit()
    db.expire_all()

    assert db.scalars(select(UserSetting)).all() == []
    assert db.scalars(select(GlobalSetting)).one().updated_by is None
    assert db.scalars(select(Job)).one().created_by is None
    assert db.scalars(select(QuotaLedger)).one().user_id is None


def test_downgrade_and_upgrade_roundtrip(engine: Engine) -> None:
    from alembic import command
    from alembic.config import Config

    from tests.conftest import ROOT, alembic_upgrade

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
    alembic_upgrade(engine)
    with engine.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master"))}
    assert {"user", "user_setting", "global_setting", "job", "job_run", "quota_ledger"} <= tables
