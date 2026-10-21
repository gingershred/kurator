"""User management: creation, authentication, roles, deactivation and deletion."""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.passwords import (
    burn_verification_time,
    hash_password,
    needs_rehash,
    password_problem,
    verify_password,
)
from app.models import Role, User

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")


class UserError(Exception):
    """A user-management rule was violated. `key` is an i18n message key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def normalize_username(username: str) -> str:
    return username.strip()


def validate_username(username: str) -> str:
    username = normalize_username(username)
    if not USERNAME_PATTERN.fullmatch(username):
        raise UserError("error.username_invalid")
    return username


def _check_password(password: str) -> None:
    problem = password_problem(password)
    if problem:
        raise UserError(problem)


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_username(db: Session, username: str) -> User | None:
    # The username column uses COLLATE NOCASE, so this lookup is case-insensitive.
    return db.scalars(select(User).where(User.username == normalize_username(username))).first()


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.username)))


def count_users(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


def _other_active_admins(db: Session, user: User) -> int:
    stmt = (
        select(func.count())
        .select_from(User)
        .where(User.role == Role.ADMIN.value, User.is_active.is_(True), User.id != user.id)
    )
    return db.scalar(stmt) or 0


def _ensure_not_last_admin(db: Session, user: User) -> None:
    if user.is_admin and user.is_active and _other_active_admins(db, user) == 0:
        raise UserError("error.last_admin")


def create_user(
    db: Session,
    username: str,
    password: str | None,
    role: Role = Role.USER,
    oidc_subject: str | None = None,
) -> User:
    username = validate_username(username)
    if get_user_by_username(db, username) is not None:
        raise UserError("error.username_taken")
    if password is not None:
        _check_password(password)
    user = User(
        username=username,
        password_hash=hash_password(password) if password is not None else None,
        role=role.value,
        oidc_subject=oidc_subject,
    )
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, username: str, password: str) -> User | None:
    """Return the active user for valid local credentials, otherwise None."""
    user = get_user_by_username(db, username)
    if user is None or user.password_hash is None:
        burn_verification_time()
        return None
    if not verify_password(user.password_hash, password):
        return None
    if not user.is_active:
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.commit()
    return user


def set_password(db: Session, user: User, new_password: str) -> None:
    """Set a new password and invalidate all existing sessions of that user."""
    _check_password(new_password)
    user.password_hash = hash_password(new_password)
    user.session_version += 1
    db.commit()


def change_own_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if user.password_hash is None or not verify_password(user.password_hash, current_password):
        raise UserError("error.current_password_wrong")
    set_password(db, user, new_password)


def set_role(db: Session, actor: User, user: User, role: Role) -> None:
    if user.role == role.value:
        return
    if role != Role.ADMIN:
        _ensure_not_last_admin(db, user)
    user.role = role.value
    user.session_version += 1
    db.commit()


def set_active(db: Session, actor: User, user: User, active: bool) -> None:
    if user.is_active == active:
        return
    if not active:
        if user.id == actor.id:
            raise UserError("error.cannot_deactivate_self")
        _ensure_not_last_admin(db, user)
        user.session_version += 1
    user.is_active = active
    db.commit()


def unlink_oidc(db: Session, user: User) -> None:
    user.oidc_subject = None
    user.session_version += 1
    db.commit()


def delete_user(db: Session, actor: User, user: User) -> None:
    """Delete a user and, via ON DELETE CASCADE, all of their personal data."""
    if user.id == actor.id:
        raise UserError("error.cannot_delete_self")
    _ensure_not_last_admin(db, user)
    db.delete(user)
    db.commit()


def set_can_download(db: Session, user: User, allowed: bool) -> None:
    user.can_download = allowed
    db.commit()
