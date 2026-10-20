"""Quota bookkeeping for the YouTube Data API.

Every API call is booked in `quota_ledger`. The quota day follows Pacific Time, because
YouTube resets the daily quota at midnight PT.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.models import QuotaLedger
from app.services.settings import get_global_setting

QUOTA_TIMEZONE = ZoneInfo("America/Los_Angeles")
SEARCH_METHOD = "search.list"

# Documented unit costs of the methods kurator uses.
METHOD_COSTS = {
    "channels.list": 1,
    "playlistItems.list": 1,
    "videos.list": 1,
    "channelSections.list": 1,
    SEARCH_METHOD: 100,
}


def quota_day(now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(QUOTA_TIMEZONE).date()


def record(
    db: Session,
    method: str,
    units: int | None = None,
    user_id: int | None = None,
    now: datetime | None = None,
) -> QuotaLedger:
    now = now or utcnow()
    entry = QuotaLedger(
        quota_day=quota_day(now),
        created_at=now,
        method=method,
        units=METHOD_COSTS[method] if units is None else units,
        user_id=user_id,
    )
    db.add(entry)
    db.commit()
    return entry


def used(db: Session, day: date, method: str | None = None) -> int:
    stmt = select(func.coalesce(func.sum(QuotaLedger.units), 0)).where(QuotaLedger.quota_day == day)
    if method is not None:
        stmt = stmt.where(QuotaLedger.method == method)
    return int(db.scalar(stmt) or 0)


def used_today(db: Session, now: datetime | None = None) -> int:
    return used(db, quota_day(now))


def search_used_today(db: Session, now: datetime | None = None) -> int:
    return used(db, quota_day(now), SEARCH_METHOD)


def can_spend(
    db: Session,
    settings: Settings,
    method: str,
    units: int | None = None,
    now: datetime | None = None,
) -> bool:
    """True if a call fits into today's budget (and the search sub-budget for search.list)."""
    cost = METHOD_COSTS[method] if units is None else units
    day = quota_day(now)
    if used(db, day) + cost > int(get_global_setting(db, settings, "quota_daily_budget")):
        return False
    if method == SEARCH_METHOD:
        search_budget = int(get_global_setting(db, settings, "quota_search_budget"))
        return used(db, day, SEARCH_METHOD) + cost <= search_budget
    return True
