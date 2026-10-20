from __future__ import annotations

import logging
from typing import Any

from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.auth import oidc
from app.auth.sessions import start_session
from app.config import Settings
from app.web.deps import get_db, get_settings_dep
from app.web.i18n import translate
from app.web.routes.auth import safe_next
from app.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oidc")

NEXT_KEY = "oidc_next"


def _client(request: Request) -> Any:
    client = getattr(request.app.state, "oidc_client", None)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return client


def _failed(request: Request, key: str, code: int) -> Response:
    context = {"next": "/", "error": translate(key), "username": ""}
    return render(request, "login.html", context, status_code=code)


@router.get("/login")
async def oidc_login(
    request: Request, next: str = "/", settings: Settings = Depends(get_settings_dep)
) -> Response:
    client = _client(request)
    request.session[NEXT_KEY] = safe_next(next)
    try:
        redirect: Response = await client.authorize_redirect(request, oidc.callback_url(settings))
    except Exception:
        log.exception("could not start OIDC login")
        return _failed(request, "login.oidc_failed", 502)
    return redirect


@router.get("/callback")
async def oidc_callback(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    client = _client(request)
    try:
        token = await client.authorize_access_token(request)
    except OAuthError as exc:
        log.warning("OIDC callback rejected: %s", exc.error)
        return _failed(request, "login.oidc_failed", 400)
    except Exception:
        log.exception("OIDC callback failed")
        return _failed(request, "login.oidc_failed", 502)

    claims = dict(token.get("userinfo") or {})
    user = oidc.resolve_user(db, claims, settings.oidc_username_claim)
    if user is None:
        return _failed(request, "login.oidc_unknown_user", 403)

    next_url = safe_next(request.session.pop(NEXT_KEY, "/"))
    start_session(request.session, db, user)
    return RedirectResponse(next_url, status.HTTP_303_SEE_OTHER)
