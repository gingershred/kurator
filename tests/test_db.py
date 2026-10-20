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
    assert {
        "channel",
        "video",
        "subscription",
        "user_video_state",
        "watch_event",
        "filter_rule",
    } <= tables


def test_deleting_user_removes_feed_data_but_keeps_shared(db: Session) -> None:
    from app.models import Channel, FilterRule, Subscription, UserVideoState, Video, WatchEvent

    user = User(username="alice", role="user")
    channel = Channel(youtube_channel_id="UC" + "a" * 22, title="Kanal")
    db.add_all([user, channel])
    db.flush()
    video = Video(
        youtube_video_id="abcdefghijk", channel_id=channel.id, published_at=datetime.now(UTC)
    )
    db.add(video)
    db.flush()
    db.add_all(
        [
            Subscription(user_id=user.id, channel_id=channel.id),
            UserVideoState(user_id=user.id, video_id=video.id, rating=1),
            WatchEvent(user_id=user.id, video_id=video.id, event="play"),
            FilterRule(user_id=user.id, type="keyword_title", value="x"),
        ]
    )
    db.commit()

    db.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": user.id})
    db.commit()

    for model in (Subscription, UserVideoState, WatchEvent, FilterRule):
        assert db.scalars(select(model)).all() == [], model
    assert db.scalars(select(Channel)).one().title == "Kanal"
    assert db.scalars(select(Video)).one().youtube_video_id == "abcdefghijk"


def test_deleting_user_removes_download_requests_but_keeps_shared_downloads(db: Session) -> None:
    from app.models import Channel, Download, DownloadRequest, Video

    user = User(username="alice", role="user")
    channel = Channel(youtube_channel_id="UC" + "a" * 22, title="Kanal")
    db.add_all([user, channel])
    db.flush()
    video = Video(
        youtube_video_id="abcdefghijk", channel_id=channel.id, published_at=datetime.now(UTC)
    )
    db.add(video)
    db.flush()
    download = Download(video_id=video.id, kind="video")
    db.add(download)
    db.flush()
    db.add(DownloadRequest(user_id=user.id, download_id=download.id))
    db.commit()
    assert not user.can_download and not user.may_download

    db.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": user.id})
    db.commit()
    assert db.scalars(select(DownloadRequest)).all() == []
    assert db.scalars(select(Download)).one().id == download.id
