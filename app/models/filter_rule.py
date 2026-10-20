from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, utcnow


class FilterRuleType(StrEnum):
    KEYWORD_TITLE = "keyword_title"
    KEYWORD_DESC = "keyword_desc"
    CHANNEL = "channel"
    MIN_DURATION = "min_duration"
    MAX_DURATION = "max_duration"
    REGEX = "regex"


class FilterRule(Base):
    __tablename__ = "filter_rule"
    __table_args__ = (
        CheckConstraint(
            "type IN ('keyword_title', 'keyword_desc', 'channel', 'min_duration', "
            "'max_duration', 'regex')",
            name="type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Keyword/regex text, channel id (as string) or duration in seconds.
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
