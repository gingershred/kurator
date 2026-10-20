"""Password hashing with argon2id."""

from __future__ import annotations

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash("kurator-dummy-password")


def burn_verification_time() -> None:
    """Spend the same time as a real verification, so unknown usernames are not detectable."""
    verify_password(_dummy_hash(), "not-the-password")


def password_problem(password: str) -> str | None:
    """Return an i18n key describing why the password is unacceptable, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return "error.password_too_short"
    if len(password) > MAX_PASSWORD_LENGTH:
        return "error.password_too_long"
    return None
