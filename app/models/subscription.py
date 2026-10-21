from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, utcnow


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    MUTED = "muted"


class SubscriptionSource(StrEnum):
    MANUAL = "manual"
    IMPORT = "import"
    RECOMMENDATION = "recommendation"


class Subscription(Base):
    __tablename__ = "subscription"
    __table_args__ = (
        UniqueConstraint("user_id", "channel_id", name="uq_subscription_user_channel"),
        CheckConstraint("status IN ('active', 'muted')", name="status"),
        CheckConstraint("source IN ('manual', 'import', 'recommendation')", name="source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("channel.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SubscriptionStatus.ACTIVE.value
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SubscriptionSource.MANUAL.value
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # Set while "download new videos automatically" is on; only videos published after
    # this moment are downloaded (no backlog).
    auto_download_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
