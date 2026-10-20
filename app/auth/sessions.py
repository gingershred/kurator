"""Session-cookie based login state.

The signed session cookie stores the user id and the user's `session_version`. Bumping the
version (password reset, deactivation, role change) invalidates all existing sessions.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.auth.csrf import rotate_csrf_token
from app.db import utcnow
from app.models import User

USER_KEY = "uid"
VERSION_KEY = "sv"


def start_session(session: dict[str, Any], db: Session, user: User) -> None:
    session.clear()
    session[USER_KEY] = user.id
    session[VERSION_KEY] = user.session_version
    rotate_csrf_token(session)
    user.last_login_at = utcnow()
    db.commit()


def end_session(session: dict[str, Any]) -> None:
    session.clear()


def user_from_session(session: dict[str, Any], db: Session) -> User | None:
    user_id = session.get(USER_KEY)
    if not isinstance(user_id, int):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.session_version != session.get(VERSION_KEY):
        session.clear()
        return None
    return user


def refresh_session(session: dict[str, Any], user: User) -> None:
    """Keep the current session valid after the user changed their own password."""
    session[VERSION_KEY] = user.session_version
