"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.auth.csrf import verify_csrf
from app.auth.ratelimit import LoginRateLimiter
from app.config import Settings, get_settings
from app.db import create_db_engine, create_session_factory
from app.web.deps import LoginRequired
from app.web.routes import (
    account,
    admin_status,
    admin_users,
    auth,
    health,
    home,
    oidc,
    thumbs,
)
from app.web.templating import render

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "web" / "static"
SESSION_COOKIE = "kurator_session"
SESSION_MAX_AGE = 30 * 24 * 3600

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "") or request.method == "POST"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = create_db_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        log.info("kurator %s started (auth mode: %s)", __version__, settings.auth_mode)
        yield
        engine.dispose()

    app = FastAPI(
        title="kurator",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(verify_csrf)],
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.rate_limiter = LoginRateLimiter()
    app.state.oidc_client = None
    if settings.oidc_enabled:
        from app.auth.oidc import create_oauth

        app.state.oidc_client = create_oauth(settings)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    # Added last, so it runs first and the session is available everywhere.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        session_cookie=SESSION_COOKIE,
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=settings.secure_cookies,
    )

    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, _exc: LoginRequired) -> Response:
        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query
        location = "/login?next=" + quote(target, safe="")
        if request.headers.get("HX-Request"):
            return Response(status_code=401, headers={"HX-Redirect": location})
        return RedirectResponse(location, status.HTTP_303_SEE_OTHER)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if not _wants_html(request) or exc.status_code < 400:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if exc.status_code == 403 and "CSRF" in str(exc.detail):
            message_key = "error.csrf"
        elif exc.status_code in (403, 404):
            message_key = f"error.{exc.status_code}"
        else:
            message_key = "error.generic"
        return render(request, "error.html", {"message_key": message_key}, exc.status_code)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(home.router)
    app.include_router(account.router)
    app.include_router(admin_users.router)
    app.include_router(admin_status.router)
    app.include_router(oidc.router)
    app.include_router(thumbs.router)
    return app
