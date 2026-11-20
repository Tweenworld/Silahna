"""Base adapter interface for public reference data sources."""

from __future__ import annotations

import abc
from typing import Any

import psycopg

from refdata.models import SourceMetadata


class ValidationError(ValueError):
    """Raised when incoming reference data fails validation checks."""


class BaseRefAdapter[T](abc.ABC):
    """Abstract interface for reference data source adapters."""

    @abc.abstractmethod
    def fetch(self, fixture_or_url: str) -> bytes:
        """Fetch raw content from local fixture path or network mirror."""

    @abc.abstractmethod
    def validate(self, raw_content: bytes, metadata: SourceMetadata) -> list[T]:
        """Validate format, schema, column requirements, and row integrity."""

    @abc.abstractmethod
    def load(self, cur: psycopg.Cursor[Any], records: list[T]) -> int:
        """Stage or load validated records into the database."""
