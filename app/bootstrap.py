"""Start-up tasks shared by the web and worker processes."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Role, User
from app.services import users

log = logging.getLogger(__name__)


def ensure_first_admin(db: Session, settings: Settings) -> User | None:
    """Create the first admin from the environment, but only if no user exists yet."""
    if users.count_users(db) > 0:
        if settings.admin_password is not None:
            log.warning(
                "KURATOR_ADMIN_PASSWORD is still set although users exist; "
                "it is ignored and can be removed from the environment"
            )
        return None
    if settings.admin_password is None:
        log.warning(
            "no users exist yet: set KURATOR_ADMIN_PASSWORD for the first start "
            "or run `python -m app.cli create-user <name> --admin`"
        )
        return None
    user = users.create_user(
        db,
        settings.admin_username,
        settings.admin_password.get_secret_value(),
        Role.ADMIN,
    )
    log.info("created first admin %r", user.username)
    return user
