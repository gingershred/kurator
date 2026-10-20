from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, utcnow


class Role(StrEnum):
    ADMIN = "admin"
    USER = "user"


class User(Base):
    __tablename__ = "user"
    __table_args__ = (CheckConstraint("role IN ('admin', 'user')", name="role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(
        String(64, collation="NOCASE"), unique=True, nullable=False
    )
    # NULL for users that only log in via OIDC.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=Role.USER.value)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    # Incremented whenever existing sessions must become invalid (password reset, role change,
    # deactivation). The session cookie stores the version it was issued for.
    session_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN.value
