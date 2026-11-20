"""In-memory data store for testing and single-node development (Part 1)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class ConsentScope(StrEnum):
    couple_compute = "couple_compute"
    counsellor_access = "counsellor_access"
    recontact = "recontact"


class ConsentState(StrEnum):
    granted = "granted"
    declined = "declined"
    pending = "pending"
    revoked = "revoked"


@dataclass
class ConsentRecord:
    person_id: str
    couple_id: str
    scope: ConsentScope
    granted: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Notification:
    notification_id: str
    person_id: str
    template_code: str
    message: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class MemoryStore:
    def __init__(self) -> None:
        self.persons: dict[str, dict[str, object]] = {}
        self.couples: dict[str, dict[str, str]] = {}
        self.consent_events: list[ConsentRecord] = []
        self.sessions: dict[str, datetime] = {}
        self.session_owners: dict[str, str] = {}
        self.referrals: list[dict[str, object]] = []
        self.notifications: list[Notification] = []
        self.pairing_attempts: dict[str, int] = {}
        self.rate_limit_records: dict[str, list[datetime]] = {}

    def create_test_couple(self, partner_a_id: str, partner_b_id: str) -> str:
        couple_id = str(uuid.uuid4())
        self.couples[couple_id] = {
            "id": couple_id,
            "partner_a_id": partner_a_id,
            "partner_b_id": partner_b_id,
        }
        self.persons[partner_a_id] = {"id": partner_a_id, "couple_id": couple_id}
        self.persons[partner_b_id] = {"id": partner_b_id, "couple_id": couple_id}
        return couple_id

    def create_session(self, person_id: str) -> str:
        token = str(uuid.uuid4())
        self.sessions[token] = datetime.now(UTC)
        self.session_owners[token] = person_id
        return token

    def is_session_valid(self, token: str, max_idle_minutes: int = 30) -> bool:
        last_seen = self.sessions.get(token)
        if not last_seen:
            return False
        now = datetime.now(UTC)
        if now - last_seen > timedelta(minutes=max_idle_minutes):
            self.revoke_session(token)
            return False
        self.sessions[token] = now
        return True

    def revoke_session(self, token: str) -> None:
        self.sessions.pop(token, None)
        self.session_owners.pop(token, None)

    def record_consent(
        self, person_id: str, couple_id: str, scope: ConsentScope, granted: bool
    ) -> ConsentRecord:
        record = ConsentRecord(
            person_id=person_id,
            couple_id=couple_id,
            scope=scope,
            granted=granted,
        )
        self.consent_events.append(record)
        return record

    def get_consent_events(self, couple_id: str) -> list[ConsentRecord]:
        return [c for c in self.consent_events if c.couple_id == couple_id]

    def get_referrals(self, couple_id: str) -> list[dict[str, object]]:
        return [r for r in self.referrals if r.get("couple_id") == couple_id]

    def add_notification(self, person_id: str, template_code: str, message: str) -> None:
        self.notifications.append(
            Notification(
                notification_id=str(uuid.uuid4()),
                person_id=person_id,
                template_code=template_code,
                message=message,
            )
        )

    def get_notifications_for_user(self, person_id: str) -> list[Notification]:
        return [n for n in self.notifications if n.person_id == person_id]


_store = MemoryStore()


def get_store() -> MemoryStore:
    return _store


def reset_store() -> None:
    global _store
    _store = MemoryStore()
