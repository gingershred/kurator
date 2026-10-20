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
FALLBACK_METHOD = "playlistItems.list"
# At most this share of the daily budget may be spent on fetching uploads via the API
# when RSS fails, so an RSS outage never starves enrichment.
FALLBACK_SHARE = 0.25

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


def fallback_budget(db: Session, settings: Settings) -> int:
    return int(int(get_global_setting(db, settings, "quota_daily_budget")) * FALLBACK_SHARE)


def can_spend_fallback(db: Session, settings: Settings, now: datetime | None = None) -> bool:
    used_fallback = used(db, quota_day(now), FALLBACK_METHOD)
    return used_fallback + METHOD_COSTS[FALLBACK_METHOD] <= fallback_budget(db, settings) and (
        can_spend(db, settings, FALLBACK_METHOD, now=now)
    )


def fallback_interval_minutes(
    db: Session, settings: Settings, channel_count: int, poll_minutes: int
) -> int:
    """How often one channel may be fetched via the API so that, even if RSS fails for all
    channels all day, the fallback stays within its share of the budget."""
    budget = fallback_budget(db, settings)
    if budget <= 0:
        return 24 * 60
    needed = -(-24 * 60 * max(channel_count, 1) // budget)  # ceiling division
    return max(poll_minutes, needed)
