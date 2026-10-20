from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.config import Settings
from app.models import User
from app.web.deps import get_settings_dep, require_user
from app.youtube.ids import is_video_id
from app.youtube.thumbs import thumb_path

router = APIRouter()

PLACEHOLDER = Path(__file__).resolve().parent.parent / "static" / "thumb-placeholder.svg"
CACHE_HEADERS = {"Cache-Control": "private, max-age=604800"}


@router.get("/thumbs/{video_id}.jpg")
def thumbnail(
    video_id: str,
    _user: User = Depends(require_user),
    settings: Settings = Depends(get_settings_dep),
) -> FileResponse:
    if not is_video_id(video_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    path = thumb_path(settings, video_id)
    if path.is_file():
        return FileResponse(path, media_type="image/jpeg", headers=CACHE_HEADERS)
    return FileResponse(
        PLACEHOLDER,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-cache"},
    )
