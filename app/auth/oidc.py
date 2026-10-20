"""Optional OpenID Connect login (e.g. Authentik, Authelia, Keycloak).

There is no self-registration: an admin creates the user first. On the first OIDC login the
configured username claim (default `preferred_username`) is matched against the kurator
username and the OIDC subject is linked. From then on only the subject counts.
"""

from __future__ import annotations

import logging
from typing import Any

from authlib.integrations.starlette_client import OAuth
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.services import users

log = logging.getLogger(__name__)

CLIENT_NAME = "kurator"
SCOPES = "openid profile email"


def create_oauth(settings: Settings) -> Any:
    """Register the OIDC client; metadata is loaded lazily via discovery."""
    if not (settings.oidc_issuer and settings.oidc_client_id and settings.oidc_client_secret):
        raise ValueError("OIDC is not configured")
    oauth = OAuth()
    oauth.register(
        name=CLIENT_NAME,
        server_metadata_url=(
            settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
        ),
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
        client_kwargs={"scope": SCOPES, "code_challenge_method": "S256"},
    )
    return oauth.create_client(CLIENT_NAME)


def callback_url(settings: Settings) -> str:
    return f"{settings.base_url}/auth/oidc/callback"


def resolve_user(db: Session, claims: dict[str, Any], username_claim: str) -> User | None:
    """Find (and on first login link) the kurator user for verified ID-token claims."""
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        return None

    user = db.scalars(select(User).where(User.oidc_subject == subject)).first()
    if user is None:
        username = claims.get(username_claim)
        if not isinstance(username, str) or not username:
            return None
        candidate = users.get_user_by_username(db, username)
        if candidate is None or candidate.oidc_subject is not None:
            log.info("OIDC login for unknown user %r rejected", username)
            return None
        candidate.oidc_subject = subject
        db.commit()
        log.info("linked OIDC subject to user %r", candidate.username)
        user = candidate

    return user if user.is_active else None
