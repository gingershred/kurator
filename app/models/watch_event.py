from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, utcnow


class WatchEventType(StrEnum):
    PLAY = "play"
    PAUSE = "pause"
    PROGRESS = "progress"
    ENDED = "ended"


class WatchEvent(Base):
    """Raw player events, kept for learning in later phases."""

    __tablename__ = "watch_event"
    __table_args__ = (
        CheckConstraint("event IN ('play', 'pause', 'progress', 'ended')", name="event"),
        Index("ix_watch_event_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("video.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    position_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
