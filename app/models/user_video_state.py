from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, utcnow


class UserVideoState(Base):
    """Per-user state of a video: watched, progress, hidden, rating, watch later."""

    __tablename__ = "user_video_state"
    __table_args__ = (CheckConstraint("rating IN (-1, 0, 1)", name="rating"),)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("video.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    watched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    progress_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    hidden_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    hidden_reason: Mapped[str | None] = mapped_column(String(255))
    rating: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    watch_later_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
