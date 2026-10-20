"""Per-user video state: watched, hidden, watch later, rating, progress."""

from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Subscription, User, UserVideoState, Video, WatchEvent, WatchEventType

MANUAL = "manual"
WATCHED_AT_FRACTION = 0.9
MAX_POSITION = 24 * 3600


def video_for_user(db: Session, user: User, youtube_video_id: str) -> Video | None:
    """A video the user may see: from a subscribed channel or with existing own state."""
    subscribed = exists().where(
        Subscription.user_id == user.id, Subscription.channel_id == Video.channel_id
    )
    has_state = exists().where(
        UserVideoState.user_id == user.id, UserVideoState.video_id == Video.id
    )
    return db.scalars(
        select(Video).where(
            Video.youtube_video_id == youtube_video_id,
            Video.is_short.is_(False),
            subscribed | has_state,
        )
    ).first()


def get_state(db: Session, user: User, video: Video) -> UserVideoState | None:
    return db.get(UserVideoState, (user.id, video.id))


def _state(db: Session, user: User, video: Video) -> UserVideoState:
    state = get_state(db, user, video)
    if state is None:
        state = UserVideoState(user_id=user.id, video_id=video.id)
        db.add(state)
    return state


def set_watched(db: Session, user: User, video: Video, watched: bool) -> UserVideoState:
    state = _state(db, user, video)
    state.watched_at = utcnow() if watched else None
    if not watched:
        state.progress_seconds = 0
    db.commit()
    return state


def set_hidden(
    db: Session, user: User, video: Video, hidden: bool, reason: str = MANUAL
) -> UserVideoState:
    state = _state(db, user, video)
    state.hidden_at = utcnow() if hidden else None
    state.hidden_reason = reason if hidden else None
    db.commit()
    return state


def set_watch_later(db: Session, user: User, video: Video, later: bool) -> UserVideoState:
    state = _state(db, user, video)
    state.watch_later_at = utcnow() if later else None
    db.commit()
    return state


def set_rating(db: Session, user: User, video: Video, rating: int) -> UserVideoState:
    if rating not in (-1, 0, 1):
        raise ValueError("rating must be -1, 0 or 1")
    state = _state(db, user, video)
    state.rating = rating
    db.commit()
    return state


def record_event(
    db: Session, user: User, video: Video, event: WatchEventType, position: int
) -> UserVideoState:
    """Store a raw player event and update progress; from 90 % on the video counts as watched."""
    limit = video.duration_seconds or MAX_POSITION
    position = max(0, min(int(position), limit))
    db.add(
        WatchEvent(user_id=user.id, video_id=video.id, event=event.value, position_seconds=position)
    )
    state = _state(db, user, video)
    if event != WatchEventType.PLAY or position > 0:
        state.progress_seconds = position
    finished = event == WatchEventType.ENDED or (
        video.duration_seconds is not None
        and position >= video.duration_seconds * WATCHED_AT_FRACTION
    )
    if finished and state.watched_at is None:
        state.watched_at = utcnow()
    db.commit()
    return state
