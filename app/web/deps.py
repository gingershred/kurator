"""FastAPI dependencies shared by all routes."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.ratelimit import LoginRateLimiter
from app.auth.sessions import user_from_session
from app.config import Settings
from app.models import User


class LoginRequired(Exception):
    """Raised when a route needs a logged-in user; handled by a redirect to the login page."""


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_rate_limiter(request: Request) -> LoginRateLimiter:
    limiter: LoginRateLimiter = request.app.state.rate_limiter
    return limiter


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user = user_from_session(request.session, db)
    request.state.user = user
    return user


def require_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise LoginRequired
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
