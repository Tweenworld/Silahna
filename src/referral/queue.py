"""Counsellor queue ordering (FR-R2, FR-U6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class QueueItem:
    referral_id: str
    tier: int
    opened_at: datetime
    assigned: bool
    overdue: bool
    rejected: bool


def sort_queue(items: list[QueueItem]) -> list[QueueItem]:
    """Confirmed shared carrier (tier 0), then incomplete (1),
    then related with no panel finding (2).
    """

    return sorted(items, key=lambda item: (item.tier, item.opened_at, item.referral_id))
