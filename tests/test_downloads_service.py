from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.models import Channel, Download, DownloadKind, Job, Role, User, Video
from app.playback import downloads
from app.playback.downloads import DownloadError
from app.services import video_state
from app.services.settings import set_global_setting
from tests.factories import add_channel, add_video, subscribe

VIDEO = DownloadKind.VIDEO


@pytest.fixture
def on(db: Session, settings: Settings) -> Settings:
    set_global_setting(db, "downloads_enabled", True)
    return settings


@pytest.fixture
def allowed(db: Session, make_user: Callable[..., User]) -> Callable[[str], User]:
    def _make(name: str) -> User:
        user = make_user(name)
        user.can_download = True
        db.commit()
        return user

    return _make


@pytest.fixture
def video(db: Session) -> Video:
    return add_video(db, add_channel(db, 1), 1)


def finish(
    db: Session, settings: Settings, download: Download, size: int = 100, age_hours: int = 0
) -> None:
    """Simulate a finished download with a real file on disk."""
    relative = f"UC1/{download.id}.mp4"
    path = downloads.media_root(settings) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    sub = downloads.media_root(settings) / f"UC1/{download.id}.de.vtt"
    sub.write_text("WEBVTT\n")
    download.status = "done"
    download.file_path = relative
    download.subtitles = {"de": f"UC1/{download.id}.de.vtt"}
    download.size_bytes = size
    download.finished_at = utcnow() - timedelta(hours=age_hours)
    db.commit()


def test_permissions(
    db: Session, settings: Settings, make_user: Callable[..., User], video: Video
) -> None:
    plain = make_user("plain")
    admin = make_user("boss", Role.ADMIN)
    with pytest.raises(DownloadError, match="not_allowed"):
        downloads.request_download(db, settings, admin, video, VIDEO)  # globally off
    set_global_setting(db, "downloads_enabled", True)
    with pytest.raises(DownloadError, match="not_allowed"):
        downloads.request_download(db, settings, plain, video, VIDEO)  # not enabled for user
    assert downloads.request_download(db, settings, admin, video, VIDEO).status == "queued"


def test_shorts_and_upcoming_are_not_downloadable(
    db: Session, on: Settings, allowed: Callable[[str], User]
) -> None:
    alice = allowed("alice")
    channel = add_channel(db, 2)
    variants: list[dict[str, Any]] = [
        {"is_short": True},
        {"live_status": "upcoming", "is_premiere": True},
        {"live_status": "live", "is_live": True},
    ]
    for n, fields in enumerate(variants):
        with pytest.raises(DownloadError, match="not_possible"):
            downloads.request_download(
                db, on, alice, add_video(db, channel, 10 + n, **fields), VIDEO
            )


def test_downloads_are_shared(
    db: Session, on: Settings, allowed: Callable[[str], User], video: Video
) -> None:
    alice, bob = allowed("alice"), allowed("bob")
    first = downloads.request_download(db, on, alice, video, VIDEO)
    second = downloads.request_download(db, on, bob, video, VIDEO)
    downloads.request_download(db, on, bob, video, VIDEO)  # idempotent
    assert first.id == second.id
    assert downloads.request_count(db, first) == 2
    assert len(db.scalars(select(Job)).all()) == 1  # downloaded only once
    audio = downloads.request_download(db, on, alice, video, DownloadKind.AUDIO)
    assert audio.id != first.id and len(db.scalars(select(Job)).all()) == 2


def test_failed_download_is_requeued(
    db: Session, on: Settings, allowed: Callable[[str], User], video: Video
) -> None:
    alice = allowed("alice")
    download = downloads.request_download(db, on, alice, video, VIDEO)
    download.status, download.error, download.error_hint = "failed", "boom", "outdated"
    db.commit()
    again = downloads.request_download(db, on, alice, video, VIDEO)
    assert again.status == "queued" and again.error is None and again.error_hint is None
    assert len(db.scalars(select(Job)).all()) == 2


def test_access(
    db: Session,
    on: Settings,
    allowed: Callable[[str], User],
    video: Video,
    make_user: Callable[..., User],
) -> None:
    alice, bob, carol = allowed("alice"), allowed("bob"), allowed("carol")
    no_rights = make_user("dave")
    channel = db.get(Channel, video.channel_id)
    assert channel is not None
    subscribe(db, carol.id, channel)  # carol follows the channel
    subscribe(db, no_rights.id, channel)
    download = downloads.request_download(db, on, alice, video, VIDEO)
    assert not downloads.can_access(db, on, alice, download)  # not finished yet
    finish(db, on, download)
    assert downloads.can_access(db, on, alice, download)  # requested it
    assert downloads.can_access(db, on, carol, download)  # video is in her feed
    assert not downloads.can_access(db, on, bob, download)  # neither
    assert not downloads.can_access(db, on, no_rights, download)  # no download permission
    set_global_setting(db, "downloads_enabled", False)
    assert not downloads.can_access(db, on, alice, download)


def test_resolve_stays_inside_media_dir(db: Session, on: Settings) -> None:
    secret = on.data_dir / "kurator.db"
    assert downloads.resolve(on, "../kurator.db") is None
    assert downloads.resolve(on, str(secret)) is None
    assert downloads.resolve(on, None) is None


def test_cancel_and_orphan_cleanup(
    db: Session, on: Settings, allowed: Callable[[str], User], video: Video
) -> None:
    alice = allowed("alice")
    download = downloads.request_download(db, on, alice, video, VIDEO)
    finish(db, on, download)
    path = downloads.resolve(on, download.file_path)
    assert path is not None
    downloads.cancel_request(db, alice, download)
    result = downloads.cleanup(db, on)
    assert result.orphaned == 1 and result.freed_bytes > 100
    assert download.status == "deleted" and not path.exists()
    assert not list(downloads.media_root(on).rglob("*.vtt"))


def test_retention_counts_from_watched_by_all(
    db: Session, on: Settings, allowed: Callable[[str], User], video: Video
) -> None:
    alice, bob = allowed("alice"), allowed("bob")
    download = downloads.request_download(db, on, alice, video, VIDEO)
    downloads.request_download(db, on, bob, video, VIDEO)
    finish(db, on, download)
    long_ago = utcnow() - timedelta(days=30)
    video_state.set_watched(db, alice, video, True).watched_at = long_ago
    db.commit()
    assert downloads.cleanup(db, on).expired == 0  # Bob has not watched it yet

    video_state.set_watched(db, bob, video, True)  # watched just now
    assert downloads.cleanup(db, on).expired == 0  # retention counts from the last viewer
    assert downloads.cleanup(db, on, now=utcnow() + timedelta(days=15)).expired == 1


def test_storage_limit_deletes_watched_then_oldest_but_never_playing(
    db: Session, on: Settings, allowed: Callable[[str], User]
) -> None:
    alice = allowed("alice")
    channel = add_channel(db, 3)
    set_global_setting(db, "download_max_storage_gb", 300 / 1024**3)  # 300 bytes
    made = []
    for n, age in enumerate([50, 40, 30, 20]):  # n=0 is the oldest
        video = add_video(db, channel, 20 + n)
        download = downloads.request_download(db, on, alice, video, VIDEO)
        finish(db, on, download, size=100, age_hours=age)
        made.append((video, download))
    video_state.set_watched(db, alice, made[2][0], True)  # newer, but watched
    downloads.mark_played(db, made[0][1])  # oldest, but playing right now

    result = downloads.cleanup(db, on)

    assert result.over_limit == 1
    assert [d.status for _v, d in made] == ["done", "done", "deleted", "done"]
    assert downloads.storage_used(db) == 300

    set_global_setting(db, "download_max_storage_gb", 150 / 1024**3)
    downloads.cleanup(db, on)
    # The playing file survives even though it is the oldest.
    assert [d.status for _v, d in made] == ["done", "deleted", "deleted", "deleted"]


def test_orphaned_queued_download_is_dropped(
    db: Session, on: Settings, allowed: Callable[[str], User], video: Video
) -> None:
    alice = allowed("alice")
    download = downloads.request_download(db, on, alice, video, VIDEO)
    downloads.cancel_request(db, alice, download)
    downloads.cleanup(db, on)
    assert download.status == "deleted"
