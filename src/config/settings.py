"""Process settings. Missing or placeholder secrets refuse to start."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDERS = frozenset(
    {
        "",
        "changeme",
        "change-me",
        "secret",
        "password",
        "replace-me",
        "todo",
        "your-secret",
        "dev-secret",
        "default",
    }
)


class SettingsError(RuntimeError):
    """Configuration is missing or unsafe."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    audit_key: str = Field(alias="AUDIT_KEY")
    session_secret: str = Field(alias="SESSION_SECRET")
    database_url: str = Field(alias="DATABASE_URL")
    genotype_key: str = Field(alias="GENOTYPE_KEY")
    migration_database_url: str | None = Field(default=None, alias="MIGRATION_DATABASE_URL")
    demo_mode: bool = Field(default=False, alias="DEMO_MODE")
    ruleset_version: str = Field(default="0.1.0", alias="RULESET_VERSION")
    audit_head_path: str = Field(default="var/audit-head.log", alias="AUDIT_HEAD_PATH")
    session_idle_seconds: int = Field(default=1800, alias="SESSION_IDLE_SECONDS")
    rate_limit_attempts: int = Field(default=5, alias="RATE_LIMIT_ATTEMPTS")
    rate_limit_window_seconds: int = Field(default=60, alias="RATE_LIMIT_WINDOW_SECONDS")
    content_root: str = Field(default="content", alias="CONTENT_ROOT")

    @field_validator("audit_key", "session_secret", "genotype_key")
    @classmethod
    def _secret_is_real(cls, value: str) -> str:
        if value.strip().lower() in _PLACEHOLDERS or len(value) < 16:
            raise ValueError("secret is missing or a known placeholder")
        return value

    @field_validator("database_url")
    @classmethod
    def _postgres_only(cls, value: str) -> str:
        if value.strip().lower() in _PLACEHOLDERS or not value.startswith("postgresql"):
            raise ValueError("DATABASE_URL must be a PostgreSQL URL")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        raise SettingsError(str(exc)) from exc
