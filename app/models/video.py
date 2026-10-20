from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, utcnow


class LiveStatus(StrEnum):
    NONE = "none"
    UPCOMING = "upcoming"
    LIVE = "live"
    COMPLETED = "completed"


class Video(Base):
    """A YouTube video, shared between all users."""

    __tablename__ = "video"
    __table_args__ = (
        CheckConstraint(
            "live_status IN ('none', 'upcoming', 'live', 'completed')", name="live_status"
        ),
        Index("ix_video_channel_id_published_at", "channel_id", "published_at"),
        Index("ix_video_published_at", "published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    youtube_video_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    channel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("channel.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # NULL until known (only the Data API provides the duration).
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    thumbnail_url: Mapped[str | None] = mapped_column(String(512))
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    category_id: Mapped[str | None] = mapped_column(String(16))
    live_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=LiveStatus.NONE.value, server_default="none"
    )
    is_short: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    is_live: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    is_premiere: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # Not detectable through official sources yet; kept for later phases.
    is_members_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    enriched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    thumb_cached_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
