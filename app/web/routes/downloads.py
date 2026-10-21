from __future__ import annotations

import mimetypes
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.models import Download, DownloadKind, User, Video
from app.playback import downloads
from app.web.deps import get_db, get_settings_dep, require_user
from app.web.routes.feed import _local_referer, own_video
from app.web.templating import flash, render

router = APIRouter()

PLAYED_UPDATE_EVERY = timedelta(minutes=1)


def require_download_user(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> User:
    """Downloads are invisible (404) unless enabled globally and for this user."""
    if not downloads.user_allowed(db, settings, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return user


def _kind(value: str) -> DownloadKind:
    try:
        return DownloadKind(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc


def _status(request: Request, db: Session, user: User, video: Video) -> Response:
    return render(
        request,
        "videos/_download.html",
        {"video": video, "dl": downloads.for_video(db, user, video)},
    )


@router.get("/videos/{youtube_video_id}/downloads")
def download_status(
    request: Request,
    youtube_video_id: str,
    user: User = Depends(require_download_user),
    db: Session = Depends(get_db),
) -> Response:
    return _status(request, db, user, own_video(db, user, youtube_video_id))


@router.post("/videos/{youtube_video_id}/download/{kind}")
def request_download(
    request: Request,
    youtube_video_id: str,
    kind: str,
    user: User = Depends(require_download_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    video = own_video(db, user, youtube_video_id)
    try:
        downloads.request_download(db, settings, user, video, _kind(kind))
    except downloads.DownloadError as exc:
        flash(request, exc.key, kind="error")
    if request.headers.get("HX-Request"):
        return _status(request, db, user, video)
    return RedirectResponse(_local_referer(request), status.HTTP_303_SEE_OTHER)


@router.post("/videos/{youtube_video_id}/download/{kind}/remove")
def remove_download(
    request: Request,
    youtube_video_id: str,
    kind: str,
    user: User = Depends(require_download_user),
    db: Session = Depends(get_db),
) -> Response:
    video = own_video(db, user, youtube_video_id)
    download = downloads.get_download(db, video, _kind(kind))
    if download is not None:
        downloads.cancel_request(db, user, download)
    if request.headers.get("HX-Request"):
        return _status(request, db, user, video)
    return RedirectResponse(_local_referer(request), status.HTTP_303_SEE_OTHER)


def _accessible(db: Session, settings: Settings, user: User, download_id: int) -> Download:
    download = db.get(Download, download_id)
    if download is None or not downloads.can_access(db, settings, user, download):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return download


@router.get("/media/{download_id}")
def media(
    download_id: int,
    user: User = Depends(require_download_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> FileResponse:
    """The downloaded file; Starlette answers Range requests, so seeking works."""
    download = _accessible(db, settings, user, download_id)
    path = downloads.resolve(settings, download.file_path)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    now = utcnow()
    if download.last_played_at is None or download.last_played_at < now - PLAYED_UPDATE_EVERY:
        downloads.mark_played(db, download, now)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix == ".m4a":
        media_type = "audio/mp4"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private"})


@router.get("/media/{download_id}/subs/{lang}.vtt")
def subtitles(
    download_id: int,
    lang: str,
    user: User = Depends(require_download_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> FileResponse:
    download = _accessible(db, settings, user, download_id)
    path = downloads.resolve(settings, download.subtitles.get(lang))
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return FileResponse(path, media_type="text/vtt; charset=utf-8")
