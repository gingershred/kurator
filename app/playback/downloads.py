"""Downloads: requests, sharing, access checks, storage accounting and cleanup.

A file is downloaded once per video and kind and shared by everyone who requested it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.models import (
    Download,
    DownloadKind,
    DownloadRequest,
    DownloadStatus,
    LiveStatus,
    RequestSource,
    User,
    UserVideoState,
    Video,
)
from app.services import jobs, video_state
from app.services.settings import get_global_setting

log = logging.getLogger(__name__)

DOWNLOAD_JOB = "download"
# A file played within this window is never deleted by the cleanup.
PLAYING_GRACE = timedelta(minutes=30)
ACTIVE = (DownloadStatus.QUEUED.value, DownloadStatus.RUNNING.value, DownloadStatus.DONE.value)


class DownloadError(Exception):
    """A download request is not possible. `key` is an i18n message key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


# --- Settings & permissions -------------------------------------------------------------------


def enabled(db: Session, settings: Settings) -> bool:
    return bool(get_global_setting(db, settings, "downloads_enabled"))


def user_allowed(db: Session, settings: Settings, user: User) -> bool:
    return enabled(db, settings) and user.may_download


def media_root(settings: Settings) -> Path:
    return settings.data_dir / "media"


def max_height(db: Session, settings: Settings) -> int:
    return int(get_global_setting(db, settings, "download_max_quality"))


def storage_limit_bytes(db: Session, settings: Settings) -> int:
    return int(float(get_global_setting(db, settings, "download_max_storage_gb")) * 1024**3)


def retention(db: Session, settings: Settings) -> timedelta:
    return timedelta(days=int(get_global_setting(db, settings, "download_retention_days")))


def storage_used(db: Session) -> int:
    used = db.scalar(
        select(func.coalesce(func.sum(Download.size_bytes), 0)).where(
            Download.status == DownloadStatus.DONE.value
        )
    )
    return int(used or 0)


# --- Requests ---------------------------------------------------------------------------------


def downloadable(video: Video) -> bool:
    return not video.is_short and video.live_status in (
        LiveStatus.NONE.value,
        LiveStatus.COMPLETED.value,
    )


def get_download(db: Session, video: Video, kind: DownloadKind) -> Download | None:
    return db.scalars(
        select(Download).where(Download.video_id == video.id, Download.kind == kind.value)
    ).first()


def request_download(
    db: Session,
    settings: Settings,
    user: User,
    video: Video,
    kind: DownloadKind,
    source: RequestSource = RequestSource.MANUAL,
) -> Download:
    if not user_allowed(db, settings, user):
        raise DownloadError("error.downloads_not_allowed")
    if not downloadable(video):
        raise DownloadError("error.download_not_possible")
    download = get_download(db, video, kind)
    queue = False
    if download is None:
        download = Download(video_id=video.id, kind=kind.value)
        db.add(download)
        db.flush()
        queue = True
    elif download.status in (DownloadStatus.FAILED.value, DownloadStatus.DELETED.value):
        download.status = DownloadStatus.QUEUED.value
        download.progress = 0
        download.error = download.error_hint = None
        download.deleted_at = None
        queue = True
    if db.get(DownloadRequest, (user.id, download.id)) is None:
        db.add(DownloadRequest(user_id=user.id, download_id=download.id, source=source.value))
    db.commit()
    if queue:
        jobs.enqueue(
            db, DOWNLOAD_JOB, {"download_id": download.id}, max_attempts=3, created_by=user.id
        )
    return download


def cancel_request(db: Session, user: User, download: Download) -> None:
    """Remove the user's own request; the cleanup deletes files nobody wants anymore."""
    request = db.get(DownloadRequest, (user.id, download.id))
    if request is not None:
        db.delete(request)
        db.commit()


def requested_by(db: Session, user: User, download: Download) -> bool:
    return db.get(DownloadRequest, (user.id, download.id)) is not None


def request_count(db: Session, download: Download) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(DownloadRequest)
            .where(DownloadRequest.download_id == download.id)
        )
        or 0
    )


@dataclass
class VideoDownloads:
    video: Download | None = None
    audio: Download | None = None
    requested: set[str] = field(default_factory=set)


def for_video(db: Session, user: User, video: Video) -> VideoDownloads:
    result = VideoDownloads()
    for download in db.scalars(select(Download).where(Download.video_id == video.id)):
        setattr(result, download.kind, download)
        if requested_by(db, user, download):
            result.requested.add(download.kind)
    return result


def can_access(db: Session, settings: Settings, user: User, download: Download) -> bool:
    """A user may play a file if they may download and either requested it or the video is in
    their own feed."""
    if not user_allowed(db, settings, user):
        return False
    if download.status != DownloadStatus.DONE.value:
        return False
    if requested_by(db, user, download):
        return True
    video = db.get(Video, download.video_id)
    return (
        video is not None
        and video_state.video_for_user(db, user, video.youtube_video_id) is not None
    )


def resolve(settings: Settings, relative: str | None) -> Path | None:
    """Absolute path of a stored file; never outside the media directory."""
    if not relative:
        return None
    root = media_root(settings).resolve()
    path = (root / relative).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def mark_played(db: Session, download: Download, now: datetime | None = None) -> None:
    download.last_played_at = now or utcnow()
    db.commit()


# --- Cleanup ----------------------------------------------------------------------------------


@dataclass
class CleanupResult:
    orphaned: int = 0
    expired: int = 0
    over_limit: int = 0
    freed_bytes: int = 0


def _watched_by_all_since(db: Session, download: Download) -> datetime | None:
    """When the last requester watched the video, or None if someone has not watched it."""
    rows = db.execute(
        select(DownloadRequest.user_id, UserVideoState.watched_at)
        .outerjoin(
            UserVideoState,
            (UserVideoState.user_id == DownloadRequest.user_id)
            & (UserVideoState.video_id == download.video_id),
        )
        .where(DownloadRequest.download_id == download.id)
    ).all()
    if not rows or any(watched is None for _uid, watched in rows):
        return None
    return max(watched for _uid, watched in rows if watched is not None)


def delete_files(settings: Settings, download: Download) -> int:
    """Remove the media file and its subtitles; returns the number of freed bytes."""
    freed = 0
    paths = [download.file_path, *download.subtitles.values()]
    for relative in paths:
        path = resolve(settings, relative)
        if path is not None:
            freed += path.stat().st_size
            path.unlink(missing_ok=True)
    return freed


def mark_deleted(db: Session, settings: Settings, download: Download, now: datetime) -> int:
    freed = delete_files(settings, download)
    download.status = DownloadStatus.DELETED.value
    download.deleted_at = now
    download.file_path = None
    download.subtitles = {}
    download.size_bytes = 0
    download.progress = 0
    return freed


def cleanup(db: Session, settings: Settings, now: datetime | None = None) -> CleanupResult:
    """Delete files nobody wants, files past retention, and the oldest ones above the limit.

    Never deletes a file that was played within the last PLAYING_GRACE.
    """
    now = now or utcnow()
    result = CleanupResult()
    keep_after = retention(db, settings)
    done = list(
        db.scalars(
            select(Download)
            .where(Download.status == DownloadStatus.DONE.value)
            .order_by(Download.finished_at, Download.id)
        )
    )

    def playing(download: Download) -> bool:
        return download.last_played_at is not None and download.last_played_at > now - PLAYING_GRACE

    remaining: list[tuple[Download, datetime | None]] = []
    for download in done:
        if playing(download):
            remaining.append((download, None))
            continue
        if request_count(db, download) == 0:
            result.freed_bytes += mark_deleted(db, settings, download, now)
            result.orphaned += 1
            continue
        watched = _watched_by_all_since(db, download)
        if watched is not None and watched + keep_after <= now:
            result.freed_bytes += mark_deleted(db, settings, download, now)
            result.expired += 1
            continue
        remaining.append((download, watched))

    limit = storage_limit_bytes(db, settings)
    used = sum(d.size_bytes for d, _w in remaining)
    # Above the limit: watched ones first, then the oldest.
    candidates = sorted(
        (item for item in remaining if not playing(item[0])),
        key=lambda item: (item[1] is None, item[0].finished_at or now),
    )
    for download, _watched in candidates:
        if used <= limit:
            break
        used -= download.size_bytes
        result.freed_bytes += mark_deleted(db, settings, download, now)
        result.over_limit += 1

    # Queued downloads nobody wants anymore are dropped as well.
    for download in db.scalars(
        select(Download).where(Download.status.in_([DownloadStatus.QUEUED.value]))
    ):
        if request_count(db, download) == 0:
            download.status = DownloadStatus.DELETED.value
            download.deleted_at = now
    db.commit()
    return result
