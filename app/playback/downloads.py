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
    Channel,
    Download,
    DownloadKind,
    DownloadRequest,
    DownloadStatus,
    LiveStatus,
    RequestSource,
    Subscription,
    SubscriptionStatus,
    User,
    UserVideoState,
    Video,
)
from app.services import jobs, video_state
from app.services.settings import get_global_setting, set_global_setting

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


def badges(db: Session, user: User, video_ids: list[int]) -> dict[int, Download]:
    """The user's own requested downloads (preferring video over audio) for a page of videos."""
    if not video_ids:
        return {}
    rows = db.scalars(
        select(Download)
        .join(DownloadRequest, DownloadRequest.download_id == Download.id)
        .where(
            DownloadRequest.user_id == user.id,
            Download.video_id.in_(video_ids),
            Download.status.in_(ACTIVE),
        )
        .order_by(Download.kind.desc())  # "video" after "audio", so video wins below
    )
    return {download.video_id: download for download in rows}


# --- Automatic downloads per channel ----------------------------------------------------------

AUTO_PER_RUN = 20


def set_auto_download(
    db: Session, settings: Settings, user: User, subscription: Subscription, on: bool
) -> None:
    if on and not user_allowed(db, settings, user):
        raise DownloadError("error.downloads_not_allowed")
    if on and subscription.auto_download_since is None:
        subscription.auto_download_since = utcnow()
    elif not on:
        subscription.auto_download_since = None
    db.commit()


def queue_auto_downloads(
    db: Session, settings: Settings, require_enriched: bool, limit: int = AUTO_PER_RUN
) -> int:
    """Request downloads of new videos for subscriptions with auto-download switched on.

    Only videos published after auto-download was enabled, never Shorts, live or upcoming
    videos; with an API key only after enrichment confirmed the video is long-form.
    """
    if not enabled(db, settings):
        return 0
    stmt = (
        select(Subscription, Video, User)
        .join(Channel, Channel.id == Subscription.channel_id)
        .join(Video, Video.channel_id == Channel.id)
        .join(User, User.id == Subscription.user_id)
        .where(
            Subscription.auto_download_since.is_not(None),
            Subscription.status == SubscriptionStatus.ACTIVE.value,
            User.is_active.is_(True),
            Video.published_at >= Subscription.auto_download_since,
            Video.is_short.is_(False),
            Video.live_status.in_([LiveStatus.NONE.value, LiveStatus.COMPLETED.value]),
        )
        .order_by(Video.published_at)
    )
    if require_enriched:
        stmt = stmt.where(Video.enriched_at.is_not(None))
    queued = 0
    for _sub, video, user in db.execute(stmt).all():
        if queued >= limit:
            break
        if not user.may_download:
            continue
        existing = get_download(db, video, DownloadKind.VIDEO)
        if existing is not None and requested_by(db, user, existing):
            continue
        try:
            request_download(db, settings, user, video, DownloadKind.VIDEO, RequestSource.AUTO)
        except DownloadError:
            continue
        queued += 1
    return queued


# --- Admin --------------------------------------------------------------------------------------

QUALITIES = (360, 480, 720, 1080, 1440, 2160)
YTDLP_MAX_AGE_DAYS = 60


@dataclass(frozen=True)
class AdminSettings:
    enabled: bool
    max_quality: int
    max_storage_gb: float
    retention_days: int
    concurrency: int
    fallback_percent: int


def admin_settings(db: Session, settings: Settings) -> AdminSettings:
    return AdminSettings(
        enabled=enabled(db, settings),
        max_quality=max_height(db, settings),
        max_storage_gb=float(get_global_setting(db, settings, "download_max_storage_gb")),
        retention_days=int(get_global_setting(db, settings, "download_retention_days")),
        concurrency=int(get_global_setting(db, settings, "download_concurrency")),
        fallback_percent=round(float(get_global_setting(db, settings, "rss_fallback_share")) * 100),
    )


def save_admin_settings(db: Session, admin: User, values: AdminSettings) -> None:
    if values.max_quality not in QUALITIES:
        raise DownloadError("error.download_settings_invalid")
    if not 0 < values.max_storage_gb <= 100_000 or not 0 <= values.retention_days <= 3650:
        raise DownloadError("error.download_settings_invalid")
    if not 1 <= values.concurrency <= 3 or not 0 <= values.fallback_percent <= 50:
        raise DownloadError("error.download_settings_invalid")
    for key, value in (
        ("downloads_enabled", values.enabled),
        ("download_max_quality", values.max_quality),
        ("download_max_storage_gb", values.max_storage_gb),
        ("download_retention_days", values.retention_days),
        ("download_concurrency", values.concurrency),
        ("rss_fallback_share", values.fallback_percent / 100),
    ):
        set_global_setting(db, key, value, updated_by=admin.id)


@dataclass(frozen=True)
class AdminRow:
    download: Download
    video: Video
    requesters: int


def admin_overview(db: Session) -> dict[str, list[AdminRow]]:
    rows = db.execute(
        select(Download, Video)
        .join(Video, Video.id == Download.video_id)
        .where(Download.status != DownloadStatus.DELETED.value)
        .order_by(Download.created_at.desc())
    ).all()
    groups: dict[str, list[AdminRow]] = {s.value: [] for s in DownloadStatus}
    for download, video in rows:
        groups[download.status].append(AdminRow(download, video, request_count(db, download)))
    return groups


def retry(db: Session, download: Download) -> None:
    download.status = DownloadStatus.QUEUED.value
    download.error = download.error_hint = None
    download.progress = 0
    db.commit()
    jobs.enqueue(db, DOWNLOAD_JOB, {"download_id": download.id}, max_attempts=3)


def delete_by_admin(db: Session, settings: Settings, download: Download) -> None:
    mark_deleted(db, settings, download, utcnow())
    db.commit()


@dataclass(frozen=True)
class Tooling:
    ytdlp_version: str
    ytdlp_age_days: int | None
    ffmpeg: bool
    deno: bool

    @property
    def ytdlp_outdated(self) -> bool:
        return self.ytdlp_age_days is not None and self.ytdlp_age_days > YTDLP_MAX_AGE_DAYS


def tooling() -> Tooling:
    import shutil

    from app.playback import ytdlp

    version = ytdlp.version()
    return Tooling(
        ytdlp_version=version,
        ytdlp_age_days=ytdlp.version_age_days(version),
        ffmpeg=shutil.which("ffmpeg") is not None,
        deno=shutil.which("deno") is not None,
    )
