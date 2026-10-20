"""Per-user and global settings stored in the database, with typed defaults."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import GlobalSetting, UserSetting

USER_DEFAULTS: dict[str, Any] = {
    "playback_mode": "embed",
    "feed_sort": "newest",
    "show_livestreams": False,
    "show_premieres": False,
    "show_members_only": False,
}

# Global values the admin can change at runtime; the start values come from the environment.
GLOBAL_KEYS_FROM_ENV = (
    "downloads_enabled",
    "download_max_quality",
    "download_max_storage_gb",
    "download_retention_days",
    "quota_daily_budget",
    "quota_search_budget",
)

WORKER_HEARTBEAT_KEY = "worker_heartbeat"


def _check_user_key(key: str) -> None:
    if key not in USER_DEFAULTS:
        raise KeyError(f"unknown user setting: {key}")


def get_user_setting(db: Session, user_id: int, key: str) -> Any:
    _check_user_key(key)
    row = db.get(UserSetting, (user_id, key))
    return USER_DEFAULTS[key] if row is None else row.value


def get_user_settings(db: Session, user_id: int) -> dict[str, Any]:
    values = dict(USER_DEFAULTS)
    rows = db.scalars(select(UserSetting).where(UserSetting.user_id == user_id))
    values.update({row.key: row.value for row in rows if row.key in USER_DEFAULTS})
    return values


def set_user_setting(db: Session, user_id: int, key: str, value: Any) -> None:
    _check_user_key(key)
    expected = type(USER_DEFAULTS[key])
    if not isinstance(value, expected):
        raise TypeError(f"{key} expects {expected.__name__}")
    row = db.get(UserSetting, (user_id, key))
    if row is None:
        db.add(UserSetting(user_id=user_id, key=key, value=value))
    else:
        row.value = value
    db.commit()


def get_global_setting(db: Session, settings: Settings, key: str) -> Any:
    if key not in GLOBAL_KEYS_FROM_ENV:
        raise KeyError(f"unknown global setting: {key}")
    row = db.get(GlobalSetting, key)
    return getattr(settings, key) if row is None else row.value


def set_global_setting(db: Session, key: str, value: Any, updated_by: int | None = None) -> None:
    row = db.get(GlobalSetting, key)
    if row is None:
        db.add(GlobalSetting(key=key, value=value, updated_by=updated_by))
    else:
        row.value = value
        row.updated_by = updated_by
    db.commit()


def record_worker_heartbeat(db: Session, now: datetime) -> None:
    set_global_setting(db, WORKER_HEARTBEAT_KEY, now.isoformat())


def worker_heartbeat(db: Session) -> datetime | None:
    row = db.get(GlobalSetting, WORKER_HEARTBEAT_KEY)
    if row is None or not isinstance(row.value, str):
        return None
    return datetime.fromisoformat(row.value)
