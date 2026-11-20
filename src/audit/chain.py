"""HMAC audit chain. The key never stays in the database."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

GENESIS = "GENESIS"


@dataclass(frozen=True)
class AuditRecord:
    prev_mac: str
    mac: str
    body: dict[str, object]


def canonical_body(body: dict[str, object]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()


def event_mac(prev_mac: str, body: dict[str, object], key: bytes) -> str:
    digest = hmac.new(key, prev_mac.encode() + b"\n" + canonical_body(body), hashlib.sha256)
    return digest.hexdigest()


def make_result_audit_record(
    couple_id: str,
    engine_version: str,
    ruleset_version: str,
    state: str,
    prev_mac: str,
    key: bytes,
) -> AuditRecord:
    """Create an audit record for a calculated result, recording versions."""
    body: dict[str, object] = {
        "action": "result_snapshot",
        "couple_id": couple_id,
        "engine_version": engine_version,
        "ruleset_version": ruleset_version,
        "state": state,
    }
    mac = event_mac(prev_mac, body, key)
    return AuditRecord(prev_mac=prev_mac, mac=mac, body=body)


def verify_chain(events: list[AuditRecord], published_head: str, key: bytes) -> bool:
    """True only when every link matches and the last MAC is the published head.

    A truncated copy of the chain fails because its last MAC is not the published head.
    """

    if not events or not published_head:
        return False
    prev = GENESIS
    for event in events:
        expected = event_mac(prev, event.body, key)
        if event.prev_mac != prev or event.mac != expected:
            return False
        prev = event.mac
    return prev == published_head
