"""Jinja2 environment and a `render` helper that adds the common template context."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import GIT_SHA, LICENSE_URL, SOURCE_URL, __version__
from app.auth.csrf import get_csrf_token
from app.config import Settings
from app.web.i18n import translate

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals.update(
    t=translate,
    version=__version__,
    git_sha=GIT_SHA[:7],
    source_url=SOURCE_URL,
    license_url=LICENSE_URL,
)


def _format_datetime(value: datetime | None, tz: str = "UTC") -> str:
    if value is None:
        return translate("common.never")
    return value.astimezone(ZoneInfo(tz)).strftime("%d.%m.%Y %H:%M")


templates.env.filters["dt"] = _format_datetime


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    settings: Settings = request.app.state.settings
    ctx: dict[str, Any] = {
        "current_user": getattr(request.state, "user", None),
        "csrf_token": get_csrf_token(request.session),
        "settings": settings,
        "tz": settings.timezone,
        "flash": pop_flash(request),
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def flash(request: Request, key: str, kind: str = "info", **kwargs: Any) -> None:
    """Store a one-time message shown on the next rendered page."""
    request.session["flash"] = {"kind": kind, "text": translate(key, **kwargs)}


def pop_flash(request: Request) -> dict[str, str] | None:
    value = request.session.pop("flash", None)
    return value if isinstance(value, dict) else None
