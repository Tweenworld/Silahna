"""Unit tests for configuration and settings fail-closed behavior (Part 0)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from config.settings import _PLACEHOLDERS, Settings, SettingsError, get_settings


def test_startup_fails_without_secrets() -> None:
    """Settings must raise SettingsError if any required secret is missing."""
    with patch.dict(os.environ, {}, clear=True):
        get_settings.cache_clear()
        with pytest.raises(SettingsError):
            get_settings()


@pytest.mark.parametrize("placeholder", sorted(_PLACEHOLDERS))
def test_startup_fails_with_placeholder_secrets(placeholder: str) -> None:
    """Settings must reject common placeholder values like 'changeme', 'secret', etc."""
    env = {
        "AUDIT_KEY": "valid_audit_key_at_least_16_chars",
        "SESSION_SECRET": "valid_session_secret_at_least_16_chars",
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/silahna",
        "GENOTYPE_KEY": "valid_genotype_key_at_least_16_chars",
    }
    for key in ("AUDIT_KEY", "SESSION_SECRET", "GENOTYPE_KEY"):
        bad_env = dict(env)
        bad_env[key] = placeholder
        with patch.dict(os.environ, bad_env, clear=True):
            with pytest.raises((ValidationError, SettingsError, ValueError)):
                Settings()


def test_startup_fails_with_short_secret() -> None:
    """Secrets shorter than 16 characters must be rejected."""
    bad_env = {
        "AUDIT_KEY": "short",
        "SESSION_SECRET": "valid_session_secret_at_least_16_chars",
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/silahna",
        "GENOTYPE_KEY": "valid_genotype_key_at_least_16_chars",
    }
    with patch.dict(os.environ, bad_env, clear=True):
        with pytest.raises((ValidationError, SettingsError, ValueError)):
            Settings()


def test_startup_fails_with_non_postgres_database_url() -> None:
    """DATABASE_URL must be a PostgreSQL connection string."""
    bad_env = {
        "AUDIT_KEY": "valid_audit_key_at_least_16_chars",
        "SESSION_SECRET": "valid_session_secret_at_least_16_chars",
        "DATABASE_URL": "sqlite:///local.db",
        "GENOTYPE_KEY": "valid_genotype_key_at_least_16_chars",
    }
    with patch.dict(os.environ, bad_env, clear=True):
        with pytest.raises((ValidationError, SettingsError, ValueError)):
            Settings()


def test_settings_valid_initialization() -> None:
    """Valid environment variables should successfully initialize Settings."""
    valid_env = {
        "AUDIT_KEY": "0123456789abcdef0123456789abcdef",
        "SESSION_SECRET": "session_secret_must_be_long_enough",
        "DATABASE_URL": "postgresql://app_rw:secret@localhost:5432/silahna",
        "GENOTYPE_KEY": "genotype_key_must_be_32_bytes_b64",
        "DEMO_MODE": "0",
    }
    with patch.dict(os.environ, valid_env, clear=True):
        settings = Settings()
        assert settings.audit_key == valid_env["AUDIT_KEY"]
        assert settings.demo_mode is False
        assert settings.ruleset_version == "0.1.0"
