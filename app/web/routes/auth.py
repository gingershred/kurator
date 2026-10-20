from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.auth.ratelimit import LoginRateLimiter
from app.auth.sessions import end_session, start_session
from app.config import Settings
from app.models import User
from app.services import users
from app.web.deps import client_ip, get_db, get_rate_limiter, get_settings_dep, optional_user
from app.web.i18n import translate
from app.web.templating import flash, render

router = APIRouter()


def safe_next(target: str | None) -> str:
    """Only allow local redirect targets (no scheme, no protocol-relative URLs)."""
    if not target or not target.startswith("/") or target.startswith(("//", "/\\")):
        return "/"
    return target


def _login_page(
    request: Request, next_url: str, error: str | None = None, username: str = "", code: int = 200
) -> Response:
    return render(
        request,
        "login.html",
        {"next": next_url, "error": error, "username": username},
        status_code=code,
    )


@router.get("/login")
def login_form(
    request: Request, next: str = "/", user: User | None = Depends(optional_user)
) -> Response:
    if user is not None:
        return RedirectResponse(safe_next(next), status.HTTP_303_SEE_OTHER)
    return _login_page(request, safe_next(next))


@router.post("/login")
def login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    limiter: LoginRateLimiter = Depends(get_rate_limiter),
) -> Response:
    next_url = safe_next(next)
    if not settings.local_login_enabled:
        return _login_page(request, next_url, translate("error.local_login_disabled"), code=403)
    ip = client_ip(request)
    wait = limiter.retry_after(ip, username)
    if wait > 0:
        message = translate("login.rate_limited", minutes=math.ceil(wait / 60))
        return _login_page(request, next_url, message, username, code=429)

    user = users.authenticate(db, username, password)
    if user is None:
        limiter.register_failure(ip, username)
        return _login_page(request, next_url, translate("login.failed"), username, code=401)

    limiter.register_success(ip, username)
    start_session(request.session, db, user)
    return RedirectResponse(next_url, status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(request: Request) -> Response:
    end_session(request.session)
    flash(request, "login.logged_out")
    return RedirectResponse("/login", status.HTTP_303_SEE_OTHER)
