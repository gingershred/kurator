"""Synchronizer-token CSRF protection.

The token lives in the (signed) session. Unsafe requests must echo it either in the form field
`csrf_token` or in the `X-CSRF-Token` header (HTMX sends it via `hx-headers`).
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import HTTPException, Request, status

SESSION_KEY = "csrf"
FORM_FIELD = "csrf_token"
HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def get_csrf_token(session: dict[str, Any]) -> str:
    token = session.get(SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_KEY] = token
    return token


def rotate_csrf_token(session: dict[str, Any]) -> str:
    session.pop(SESSION_KEY, None)
    return get_csrf_token(session)


async def verify_csrf(request: Request) -> None:
    """FastAPI dependency applied to the whole app."""
    if request.method in SAFE_METHODS:
        return
    expected = request.session.get(SESSION_KEY)
    sent = request.headers.get(HEADER)
    if not sent:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            value = form.get(FORM_FIELD)
            sent = value if isinstance(value, str) else None
    if not expected or not sent or not secrets.compare_digest(str(expected), sent):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="CSRF token missing or invalid")
