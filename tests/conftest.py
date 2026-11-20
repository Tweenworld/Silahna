"""Global test configuration and fixtures."""

from __future__ import annotations

import pytest

from config.settings import get_settings
from db.memory_store import reset_store


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure clean, valid settings and reset in-memory database between tests."""
    monkeypatch.setenv("AUDIT_KEY", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("SESSION_SECRET", "session_secret_for_tests_32_bytes!")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app_rw:secret@localhost:5432/silahna")
    monkeypatch.setenv("GENOTYPE_KEY", "genotype_key_must_be_32_bytes_b64")
    monkeypatch.setenv("DEMO_MODE", "0")
    get_settings.cache_clear()
    reset_store()
    yield
    get_settings.cache_clear()
    reset_store()
