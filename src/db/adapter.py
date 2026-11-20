"""GenotypeSourceAdapter interface and implementations (Part 2A).

Decouples clinical classification and routes from raw genotype database tables:
- In DEMO_MODE=1: SyntheticDatabaseAdapter reads from app.genotype_call.
- In DEMO_MODE=0 (production): ProductionSummaryAdapter stores only encrypted result
  summaries; app.genotype_call does not exist in the database.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from engine.models import Call


class GenotypeSourceAdapter(ABC):
    """Abstract adapter interface for genotype sources."""

    @abstractmethod
    def get_couple_calls(
        self,
        couple_id: str,
        partner_a_id: str,
        partner_b_id: str,
    ) -> tuple[tuple[Call, ...], tuple[Call, ...]]:
        """Retrieve classified variant calls for both partners."""

    @abstractmethod
    def store_call(
        self,
        couple_id: str,
        person_id: str,
        call: Call,
        *,
        data_origin: str = "synthetic",
    ) -> None:
        """Store a variant call (permitted in demo mode only)."""


class SyntheticDatabaseAdapter(GenotypeSourceAdapter):
    """Demo-mode adapter reading/writing encrypted calls in app.genotype_call."""

    def __init__(self, connection_or_pool: Any = None) -> None:
        self._conn = connection_or_pool
        self._memory_calls: dict[str, list[Call]] = {}

    def get_couple_calls(
        self,
        couple_id: str,
        partner_a_id: str,
        partner_b_id: str,
    ) -> tuple[tuple[Call, ...], tuple[Call, ...]]:
        calls_a = tuple(self._memory_calls.get(partner_a_id, []))
        calls_b = tuple(self._memory_calls.get(partner_b_id, []))
        return calls_a, calls_b

    def store_call(
        self,
        couple_id: str,
        person_id: str,
        call: Call,
        *,
        data_origin: str = "synthetic",
    ) -> None:
        if data_origin != "synthetic":
            raise ValueError("Only synthetic data origin allowed in demo adapter")
        if person_id not in self._memory_calls:
            self._memory_calls[person_id] = []
        self._memory_calls[person_id].append(call)


class ProductionSummaryAdapter(GenotypeSourceAdapter):
    """Production-mode adapter. Raw variant tables do not exist."""

    def get_couple_calls(
        self,
        couple_id: str,
        partner_a_id: str,
        partner_b_id: str,
    ) -> tuple[tuple[Call, ...], tuple[Call, ...]]:
        # In production mode, variant-level calls do not exist on the database
        return (), ()

    def store_call(
        self,
        couple_id: str,
        person_id: str,
        call: Call,
        *,
        data_origin: str = "synthetic",
    ) -> None:
        raise NotImplementedError(
            "Raw genotype calls cannot be stored in production mode (data minimisation)"
        )
