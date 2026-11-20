"""Regression and abuse test suite for the 11 Security Findings (Part 1).

These tests pin the security findings as explicit executable specifications:
1. test_login_route_404_without_demo_mode
2. test_cookie_signed_with_old_default_rejected
3. test_fresh_start_has_no_persons
4. test_non_member_consent_403_and_writes_nothing
5. test_logout_invalidates_cookie, test_session_expires
6. test_security_headers_present
7. test_rate_limit_returns_429
8. test_unknown_and_foreign_couple_identical
9. test_get_creates_no_referral
10. test_invalid_scope_422
11. test_truncation_detected_against_published_head
"""

from __future__ import annotations

import os
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

from audit.chain import AuditRecord, event_mac, verify_chain
from audit.publish_head import publish_head, read_published_head


# Finding 11: Audit chain truncation detection against external published head
def test_truncation_detected_against_published_head(tmp_path: Path) -> None:
    """Verify that an attacker truncating the audit log is detected against the published head."""
    key = b"secret_audit_key_32_bytes_test!!"
    head_file = tmp_path / "published-head.log"

    # Generate 3 events
    e1_mac = event_mac("GENESIS", {"action": "consent_granted", "user": "u1"}, key)
    r1 = AuditRecord(
        prev_mac="GENESIS", mac=e1_mac, body={"action": "consent_granted", "user": "u1"}
    )

    e2_mac = event_mac(e1_mac, {"action": "view_result", "user": "u1"}, key)
    r2 = AuditRecord(prev_mac=e1_mac, mac=e2_mac, body={"action": "view_result", "user": "u1"})

    e3_mac = event_mac(e2_mac, {"action": "referral_created", "user": "c1"}, key)
    r3 = AuditRecord(prev_mac=e2_mac, mac=e3_mac, body={"action": "referral_created", "user": "c1"})

    # Publish head
    publish_head(head_file, e3_mac)
    assert read_published_head(head_file) == e3_mac

    # Full chain verifies
    assert verify_chain([r1, r2, r3], read_published_head(head_file), key) is True

    # Truncated chain (attacker deleted event 3) MUST fail
    assert verify_chain([r1, r2], read_published_head(head_file), key) is False


# Finding 1: Open /login/{pid} must return 404 when DEMO_MODE=0
def test_login_route_404_without_demo_mode() -> None:
    """Demo login route must return 404 in production mode (DEMO_MODE=0)."""
    # This will be verified against the FastAPI test client in Part 1
    from fastapi.testclient import TestClient

    from web.main import create_app

    with patch.dict(os.environ, {"DEMO_MODE": "0"}, clear=False):
        app = create_app()
        client = TestClient(app)
        response = client.get("/login/test_person")
        assert response.status_code == 404


# Finding 2: Cookie signed with old or default secrets must be rejected
def test_cookie_signed_with_old_default_rejected() -> None:
    """Session cookies signed with old default or placeholder keys must be rejected."""
    import itsdangerous
    from fastapi.testclient import TestClient

    from web.main import create_app

    app = create_app()
    client = TestClient(app)

    # Sign cookie with old/default insecure key
    signer = itsdangerous.TimestampSigner("old_default_insecure_key")
    bad_cookie = signer.sign("person_123").decode()

    client.cookies.set("session", bad_cookie)
    response = client.get("/api/me")
    assert response.status_code == 401


# Finding 3: Fresh start has zero seeded accounts at import
def test_fresh_start_has_no_persons() -> None:
    """Application import or startup must never automatically seed database accounts."""
    from db.memory_store import get_store
    from web.main import create_app

    _ = create_app()
    store = get_store()
    assert len(store.persons) == 0


# Finding 4: Non-member consent attempt returns 403 and writes nothing
def test_non_member_consent_403_and_writes_nothing() -> None:
    """A user attempting to write consent for a couple they do not belong to is rejected."""
    from fastapi.testclient import TestClient

    from db.memory_store import get_store
    from web.main import create_app

    app = create_app()
    client = TestClient(app)
    store = get_store()

    # Create couple with persons A and B
    couple_id = store.create_test_couple("pA", "pB")

    # Person C (non-member) attempts to record consent
    client.cookies.set("session", store.create_session("pC"))
    res = client.post(
        f"/api/couples/{couple_id}/consent",
        json={"scope": "couple_compute", "granted": True},
    )
    assert res.status_code in (403, 404)
    assert len(store.get_consent_events(couple_id)) == 0


# Finding 5: Logout invalidates cookie & idle expiry
def test_logout_invalidates_cookie() -> None:
    """Logging out revokes the session on the server and expires the cookie."""
    from fastapi.testclient import TestClient

    from db.memory_store import get_store
    from web.main import create_app

    app = create_app()
    client = TestClient(app)
    store = get_store()

    token = store.create_session("pA")
    client.cookies.set("session", token)

    res = client.post("/logout")
    assert res.status_code == 200
    assert store.is_session_valid(token) is False

    # Subsequent request with revoked cookie fails
    res_me = client.get("/api/me")
    assert res_me.status_code == 401


def test_session_expires() -> None:
    """Sessions past the idle window (30 min) must be rejected."""
    from datetime import datetime, timedelta

    from fastapi.testclient import TestClient

    from db.memory_store import get_store
    from web.main import create_app

    app = create_app()
    client = TestClient(app)
    store = get_store()

    token = store.create_session("pA")
    # Artificially age the session past 30 mins
    store.sessions[token] = datetime.now(UTC) - timedelta(minutes=31)

    client.cookies.set("session", token)
    res = client.get("/api/me")
    assert res.status_code == 401


# Finding 6: Security headers present
def test_security_headers_present() -> None:
    """Responses must contain mandatory security headers."""
    from fastapi.testclient import TestClient

    from web.main import create_app

    app = create_app()
    client = TestClient(app)
    res = client.get("/health")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in res.headers["Content-Security-Policy"]


# Finding 7: Rate limiting on authentication and write routes
def test_rate_limit_returns_429() -> None:
    """Rapid repeated requests exceeding rate limit threshold return HTTP 429."""
    from fastapi.testclient import TestClient

    from web.main import create_app

    app = create_app()
    client = TestClient(app)

    # Exceed rate limit on pairing route
    statuses = [
        client.post("/api/pairing/claim", json={"code": "123456"}).status_code for _ in range(10)
    ]
    assert 429 in statuses


# Finding 8: Uniform response for unknown couple and non-member couple
def test_unknown_and_foreign_couple_identical() -> None:
    """To prevent enumeration, unknown couple and foreign couple responses must be identical."""
    from fastapi.testclient import TestClient

    from db.memory_store import get_store
    from web.main import create_app

    app = create_app()
    client = TestClient(app)
    store = get_store()

    # User A is in couple 1
    _ = store.create_test_couple("pA", "pB")
    # Couple 2 has persons X and Y
    couple_2 = store.create_test_couple("pX", "pY")

    client.cookies.set("session", store.create_session("pA"))

    # Request foreign couple (couple 2) vs non-existent couple
    res_foreign = client.get(f"/api/couples/{couple_2}")
    res_unknown = client.get("/api/couples/00000000-0000-0000-0000-000000000000")

    assert res_foreign.status_code == res_unknown.status_code
    assert res_foreign.json() == res_unknown.json()


# Finding 9: GET result views never create referrals
def test_get_creates_no_referral() -> None:
    """Querying results via GET must be idempotent and never mutate referral state."""
    from fastapi.testclient import TestClient

    from db.memory_store import get_store
    from web.main import create_app

    app = create_app()
    client = TestClient(app)
    store = get_store()

    couple_id = store.create_test_couple("pA", "pB")
    client.cookies.set("session", store.create_session("pA"))

    res = client.get(f"/api/couples/{couple_id}/result")
    assert res.status_code in (200, 403, 404)
    assert len(store.get_referrals(couple_id)) == 0


# Finding 10: Validation returns explicit 422, never 500 assert failure
def test_invalid_scope_422() -> None:
    """Invalid payload formats must return HTTP 422 Unprocessable Entity, not an assertion error."""
    from fastapi.testclient import TestClient

    from db.memory_store import get_store
    from web.main import create_app

    app = create_app()
    client = TestClient(app)
    store = get_store()

    couple_id = store.create_test_couple("pA", "pB")
    client.cookies.set("session", store.create_session("pA"))

    # Pass invalid scope
    res = client.post(f"/api/couples/{couple_id}/consent", json={"scope": "invalid_scope_123"})
    assert res.status_code == 422
