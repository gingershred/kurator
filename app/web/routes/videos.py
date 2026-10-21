from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings
from app.feed.query import FeedItem
from app.models import Channel, Subscription, User, WatchEventType
from app.playback import downloads
from app.playback.embed import PLAYER_CSP, choose_mode, playback_for
from app.services import filters, video_state
from app.services import settings as user_settings
from app.web.deps import get_db, get_settings_dep, require_user
from app.web.routes.feed import own_video
from app.web.templating import render

router = APIRouter(prefix="/videos")


class PlayerEvent(BaseModel):
    event: WatchEventType
    position: int = Field(ge=0)


@router.get("/{youtube_video_id}")
def video_page(
    request: Request,
    youtube_video_id: str,
    play: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    video = own_video(db, user, youtube_video_id)
    channel = db.get(Channel, video.channel_id)
    if channel is None:  # pragma: no cover - FK guarantees it
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    sub = db.query(Subscription).filter_by(user_id=user.id, channel_id=video.channel_id).first()
    state = video_state.get_state(db, user, video)
    prefs = user_settings.get_user_settings(db, user.id)

    allowed = downloads.user_allowed(db, settings, user)
    dl = downloads.for_video(db, user, video) if allowed else None
    local = (
        dl.video if dl and dl.video and downloads.can_access(db, settings, user, dl.video) else None
    )
    audio = (
        dl.audio if dl and dl.audio and downloads.can_access(db, settings, user, dl.audio) else None
    )
    mode = choose_mode(
        play,
        str(prefs["playback_mode"]) if allowed else "embed",
        local is not None,
        audio is not None,
    )
    response = render(
        request,
        "videos/detail.html",
        {
            "item": FeedItem(video, channel, sub, state),
            "video": video,
            "view": "detail",
            "playback": playback_for(video, state, mode),
            "downloads_allowed": allowed,
            "dl": dl,
            "local": local,
            "audio": audio,
            "channel_titles": filters.channel_titles(db, user),
        },
    )
    response.headers["Content-Security-Policy"] = PLAYER_CSP
    return response


@router.post("/{youtube_video_id}/events")
def player_event(
    youtube_video_id: str,
    payload: PlayerEvent,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    video = own_video(db, user, youtube_video_id)
    state = video_state.record_event(db, user, video, payload.event, payload.position)
    return JSONResponse(
        {"progress": state.progress_seconds, "watched": state.watched_at is not None}
    )
