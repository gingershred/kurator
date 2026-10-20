from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.config import Settings
from app.services.settings import set_global_setting
from app.youtube import quota


def test_quota_day_uses_pacific_time() -> None:
    # 07:30 UTC is still the previous day in California (UTC-7 in summer).
    assert quota.quota_day(datetime(2026, 7, 2, 6, 59, tzinfo=UTC)) == date(2026, 7, 1)
    assert quota.quota_day(datetime(2026, 7, 2, 7, 0, tzinfo=UTC)) == date(2026, 7, 2)


def test_record_and_sum(db: Session) -> None:
    now = datetime(2026, 7, 2, 12, tzinfo=UTC)
    quota.record(db, "videos.list", now=now)
    quota.record(db, "search.list", user_id=None, now=now)
    quota.record(db, "videos.list", now=datetime(2026, 7, 1, 12, tzinfo=UTC))  # other day
    assert quota.used_today(db, now) == 101
    assert quota.search_used_today(db, now) == 100


def test_can_spend_respects_budgets(db: Session, settings: Settings) -> None:
    now = datetime(2026, 7, 2, 12, tzinfo=UTC)
    set_global_setting(db, "quota_daily_budget", 250)
    set_global_setting(db, "quota_search_budget", 100)
    assert quota.can_spend(db, settings, "search.list", now=now)
    quota.record(db, "search.list", now=now)
    assert not quota.can_spend(db, settings, "search.list", now=now)  # search budget used up
    assert quota.can_spend(db, settings, "videos.list", units=150, now=now)
    quota.record(db, "videos.list", units=150, now=now)
    assert not quota.can_spend(db, settings, "videos.list", now=now)  # daily budget exhausted


def test_can_spend_defaults_from_env(db: Session, settings: Settings) -> None:
    assert settings.quota_daily_budget == 8000
    assert quota.can_spend(db, settings, "videos.list", units=8000)
    assert not quota.can_spend(db, settings, "videos.list", units=8001)
