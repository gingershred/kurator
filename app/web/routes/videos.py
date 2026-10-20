from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.feed.query import FeedItem
from app.models import Channel, Subscription, User, WatchEventType
from app.playback.embed import PLAYER_CSP, playback_for
from app.services import filters, video_state
from app.services import settings as user_settings
from app.web.deps import get_db, require_user
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
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    video = own_video(db, user, youtube_video_id)
    channel = db.get(Channel, video.channel_id)
    if channel is None:  # pragma: no cover - FK guarantees it
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    sub = db.query(Subscription).filter_by(user_id=user.id, channel_id=video.channel_id).first()
    state = video_state.get_state(db, user, video)
    prefs = user_settings.get_user_settings(db, user.id)
    response = render(
        request,
        "videos/detail.html",
        {
            "item": FeedItem(video, channel, sub, state),
            "view": "detail",
            "playback": playback_for(video, state, str(prefs["playback_mode"])),
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
