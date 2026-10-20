from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, utcnow


class DownloadKind(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"


class DownloadStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DELETED = "deleted"


class RequestSource(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


class Download(Base):
    """A downloaded file, shared between all users who requested it."""

    __tablename__ = "download"
    __table_args__ = (
        UniqueConstraint("video_id", "kind", name="uq_download_video_kind"),
        CheckConstraint("kind IN ('video', 'audio')", name="kind"),
        CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed', 'deleted')", name="status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("video.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False, default=DownloadKind.VIDEO.value)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DownloadStatus.QUEUED.value, index=True
    )
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Relative to <data>/media.
    file_path: Mapped[str | None] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    height: Mapped[int | None] = mapped_column(Integer)
    format: Mapped[str | None] = mapped_column(String(128))
    # Language code -> relative path of a WebVTT file.
    subtitles: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    error_hint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_played_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class DownloadRequest(Base):
    """Which user asked for which download (n:m)."""

    __tablename__ = "download_request"
    __table_args__ = (CheckConstraint("source IN ('manual', 'auto')", name="source"),)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    download_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("download.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    source: Mapped[str] = mapped_column(
        String(8), nullable=False, default=RequestSource.MANUAL.value
    )
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
