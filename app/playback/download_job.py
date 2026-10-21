"""Worker side of downloads: run one queued download with yt-dlp."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import utcnow
from app.models import Channel, Download, DownloadKind, DownloadStatus, Job, Video
from app.playback import downloads, ytdlp
from app.services.jobs import Postpone

log = logging.getLogger(__name__)

POSTPONE_DISABLED = 15 * 60
POSTPONE_STORAGE_FULL = 30 * 60
HINT_STORAGE_FULL = "storage_full"
MAX_ERROR_LENGTH = 2000


def _progress_updater(
    session_factory: sessionmaker[Session], download_id: int
) -> ytdlp.ProgressCallback:
    def update(percent: int) -> None:
        with session_factory() as db:
            download = db.get(Download, download_id)
            if download is not None:
                download.progress = percent
                db.commit()

    return update


def run(
    db: Session,
    settings: Settings,
    session_factory: sessionmaker[Session],
    job: Job | None,
    download_id: int,
) -> None:
    download = db.get(Download, download_id)
    if download is None or download.status not in (
        DownloadStatus.QUEUED.value,
        DownloadStatus.RUNNING.value,
    ):
        return
    if downloads.request_count(db, download) == 0:
        download.status = DownloadStatus.DELETED.value
        download.deleted_at = utcnow()
        db.commit()
        return
    if not downloads.enabled(db, settings):
        raise Postpone(POSTPONE_DISABLED, "downloads are disabled")
    if downloads.storage_used(db) >= downloads.storage_limit_bytes(db, settings):
        downloads.cleanup(db, settings)
        if downloads.storage_used(db) >= downloads.storage_limit_bytes(db, settings):
            download.error_hint = HINT_STORAGE_FULL
            db.commit()
            raise Postpone(POSTPONE_STORAGE_FULL, "storage limit reached")

    video = db.get(Video, download.video_id)
    channel = db.get(Channel, video.channel_id) if video is not None else None
    if video is None or channel is None:
        download.status = DownloadStatus.FAILED.value
        download.error = "video no longer exists"
        db.commit()
        return

    kind = DownloadKind(download.kind)
    basename = video.youtube_video_id + (".audio" if kind == DownloadKind.AUDIO else "")
    root = downloads.media_root(settings)
    spec = ytdlp.DownloadSpec(
        youtube_video_id=video.youtube_video_id,
        kind=kind.value,
        target_dir=root / channel.youtube_channel_id,
        basename=basename,
        max_height=downloads.max_height(db, settings),
        subtitle_langs=tuple(dict.fromkeys([settings.language, "en"])),
    )
    download.status = DownloadStatus.RUNNING.value
    download.progress = 0
    download.error = download.error_hint = None
    db.commit()

    try:
        result = ytdlp.run_download(spec, _progress_updater(session_factory, download.id))
    except ytdlp.DownloadFailed as exc:
        db.refresh(download)
        download.error = str(exc)[:MAX_ERROR_LENGTH]
        download.error_hint = exc.hint
        final = not exc.retryable or job is None or job.attempts >= job.max_attempts
        download.status = (DownloadStatus.FAILED if final else DownloadStatus.QUEUED).value
        if not exc.retryable and job is not None:
            job.max_attempts = job.attempts  # no further attempts
        db.commit()
        raise

    db.refresh(download)
    download.file_path = str(result.file_path.relative_to(root))
    download.subtitles = {
        lang: str(path.relative_to(root)) for lang, path in result.subtitles.items()
    }
    download.size_bytes = result.size_bytes
    download.height = result.height
    download.format = result.format
    download.status = DownloadStatus.DONE.value
    download.progress = 100
    download.finished_at = utcnow()
    db.commit()
    log.info("downloaded %s (%s, %d bytes)", video.youtube_video_id, kind.value, result.size_bytes)
    downloads.cleanup(db, settings)


def recover_interrupted(db: Session) -> int:
    """Downloads left 'running' by a stopped worker go back into the queue."""
    count = 0
    for download in db.query(Download).filter(Download.status == DownloadStatus.RUNNING.value):
        download.status = DownloadStatus.QUEUED.value
        count += 1
    db.commit()
    return count
