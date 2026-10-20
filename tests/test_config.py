from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings

SECRET = "x" * 32


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KURATOR_SECRET_KEY", SECRET)
    settings = Settings()
    assert settings.auth_mode == "local"
    assert settings.quota_daily_budget == 8000
    assert settings.shorts_max_seconds == 180
    assert settings.db_path == Path("/data/kurator.db")
    assert settings.local_login_enabled
    assert not settings.oidc_enabled


def test_secret_key_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KURATOR_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_short_secret_key_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(KURATOR_SECRET_KEY="short")


def test_empty_env_values_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KURATOR_SECRET_KEY", SECRET)
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    monkeypatch.setenv("QUOTA_DAILY_BUDGET", "")
    settings = Settings()
    assert settings.youtube_api_key is None
    assert settings.quota_daily_budget == 8000


def test_oidc_mode_requires_client_config() -> None:
    with pytest.raises(ValidationError, match="OIDC_ISSUER"):
        Settings(KURATOR_SECRET_KEY=SECRET, KURATOR_AUTH_MODE="oidc")
    settings = Settings(
        KURATOR_SECRET_KEY=SECRET,
        KURATOR_AUTH_MODE="both",
        OIDC_ISSUER="https://idp.example",
        OIDC_CLIENT_ID="kurator",
        OIDC_CLIENT_SECRET="s3cret",
    )
    assert settings.oidc_enabled and settings.local_login_enabled


def test_search_budget_must_fit_daily_budget() -> None:
    with pytest.raises(ValidationError):
        Settings(KURATOR_SECRET_KEY=SECRET, QUOTA_DAILY_BUDGET=100, QUOTA_SEARCH_BUDGET=200)


def test_invalid_timezone_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(KURATOR_SECRET_KEY=SECRET, KURATOR_TIMEZONE="Mars/Base")


def test_secrets_not_in_repr() -> None:
    settings = Settings(KURATOR_SECRET_KEY=SECRET, YOUTUBE_API_KEY="AIza-very-secret")
    assert SECRET not in repr(settings)
    assert "AIza-very-secret" not in repr(settings)
