"""Central configuration loaded from environment variables (and an optional `.env` file).

Environment variables only hold infrastructure and start values. Everything users or admins
change at runtime lives in the database.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AuthMode = Literal["local", "oidc", "both"]
EmbeddingProvider = Literal["local", "openai_compatible"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    # Basis
    secret_key: SecretStr = Field(validation_alias="KURATOR_SECRET_KEY")
    base_url: str = Field("http://localhost:8080", validation_alias="KURATOR_BASE_URL")
    timezone: str = Field("Europe/Berlin", validation_alias="KURATOR_TIMEZONE")
    language: str = Field("de", validation_alias="KURATOR_LANGUAGE")
    data_dir: Path = Field(Path("/data"), validation_alias="KURATOR_DATA_DIR")
    secure_cookies: bool = Field(False, validation_alias="KURATOR_SECURE_COOKIES")
    log_level: str = Field("INFO", validation_alias="KURATOR_LOG_LEVEL")

    # First admin
    admin_username: str = Field("admin", validation_alias="KURATOR_ADMIN_USERNAME")
    admin_password: SecretStr | None = Field(None, validation_alias="KURATOR_ADMIN_PASSWORD")

    # Login
    auth_mode: AuthMode = Field("local", validation_alias="KURATOR_AUTH_MODE")
    oidc_issuer: str | None = Field(None, validation_alias="OIDC_ISSUER")
    oidc_client_id: str | None = Field(None, validation_alias="OIDC_CLIENT_ID")
    oidc_client_secret: SecretStr | None = Field(None, validation_alias="OIDC_CLIENT_SECRET")
    oidc_username_claim: str = Field("preferred_username", validation_alias="OIDC_USERNAME_CLAIM")

    # YouTube
    youtube_api_key: SecretStr | None = Field(None, validation_alias="YOUTUBE_API_KEY")
    quota_daily_budget: int = Field(8000, ge=0, le=10000, validation_alias="QUOTA_DAILY_BUDGET")
    quota_search_budget: int = Field(2000, ge=0, validation_alias="QUOTA_SEARCH_BUDGET")
    feed_poll_minutes: int = Field(30, ge=5, validation_alias="FEED_POLL_MINUTES")
    shorts_max_seconds: int = Field(180, ge=0, validation_alias="SHORTS_MAX_SECONDS")

    # Downloads
    downloads_enabled: bool = Field(False, validation_alias="DOWNLOADS_ENABLED")
    download_max_quality: int = Field(1080, ge=144, validation_alias="DOWNLOAD_MAX_QUALITY")
    download_max_storage_gb: float = Field(50, gt=0, validation_alias="DOWNLOAD_MAX_STORAGE_GB")
    download_retention_days: int = Field(14, ge=0, validation_alias="DOWNLOAD_RETENTION_DAYS")

    # LLM
    llm_enabled: bool = Field(False, validation_alias="LLM_ENABLED")
    llm_base_url: str | None = Field(None, validation_alias="LLM_BASE_URL")
    llm_api_key: SecretStr | None = Field(None, validation_alias="LLM_API_KEY")
    llm_model: str | None = Field(None, validation_alias="LLM_MODEL")

    # Embeddings
    embedding_provider: EmbeddingProvider = Field("local", validation_alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(
        "intfloat/multilingual-e5-small", validation_alias="EMBEDDING_MODEL"
    )
    embedding_base_url: str | None = Field(None, validation_alias="EMBEDDING_BASE_URL")
    embedding_api_key: SecretStr | None = Field(None, validation_alias="EMBEDDING_API_KEY")

    @field_validator("secret_key")
    @classmethod
    def _secret_key_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 16:
            raise ValueError("KURATOR_SECRET_KEY must be at least 16 characters long")
        return value

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone: {value}") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unknown log level: {value}")
        return value

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _check_combinations(self) -> Self:
        if self.quota_search_budget > self.quota_daily_budget:
            raise ValueError("QUOTA_SEARCH_BUDGET must not exceed QUOTA_DAILY_BUDGET")
        if self.oidc_enabled and not (
            self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret
        ):
            raise ValueError(
                "KURATOR_AUTH_MODE=oidc|both requires OIDC_ISSUER, OIDC_CLIENT_ID "
                "and OIDC_CLIENT_SECRET"
            )
        if self.llm_enabled and not (self.llm_base_url and self.llm_model):
            raise ValueError("LLM_ENABLED=true requires LLM_BASE_URL and LLM_MODEL")
        if self.embedding_provider == "openai_compatible" and not self.embedding_base_url:
            raise ValueError("EMBEDDING_PROVIDER=openai_compatible requires EMBEDDING_BASE_URL")
        return self

    @property
    def local_login_enabled(self) -> bool:
        return self.auth_mode in ("local", "both")

    @property
    def oidc_enabled(self) -> bool:
        return self.auth_mode in ("oidc", "both")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kurator.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"


@lru_cache
def get_settings() -> Settings:
    """Load and validate settings once per process."""
    return Settings()
