"""Adversarial tests for privacy side-channels:
- test_access_log_excludes_partner_events
- test_no_notification_on_partner_decline_or_revoke
- test_consent_check_timing_and_work_equivalence
"""

from __future__ import annotations


def test_access_log_excludes_partner_events() -> None:
    """GET /api/me/access-log must only show user's own accesses
    and never partner consent changes.
    """
    from db.memory_store import get_store
    from fastapi.testclient import TestClient
    from web.main import create_app

    app = create_app()
    client = TestClient(app)
    store = get_store()

    couple_id = store.create_test_couple("partnerA", "partnerB")

    # Partner B revokes consent
    client.cookies.set("session", store.create_session("partnerB"))
    client.post(
        f"/api/couples/{couple_id}/consent", json={"scope": "couple_compute", "granted": False}
    )

    # Partner A checks their access log
    client.cookies.set("session", store.create_session("partnerA"))
    res = client.get("/api/me/access-log")
    assert res.status_code == 200
    events = res.json().get("events", [])

    # Assert ZERO partner revocation or decline events are surfaced to partner A
    for event in events:
        assert "partnerB" not in str(event)
        assert "revoked" not in event.get("action", "").lower()
        assert "declined" not in event.get("action", "").lower()


def test_no_notification_on_partner_decline_or_revoke() -> None:
    """Notifications must NEVER be dispatched conditionally when one partner declines or revokes."""
    from db.memory_store import get_store
    from fastapi.testclient import TestClient
    from web.main import create_app

    app = create_app()
    client = TestClient(app)
    store = get_store()

    couple_id = store.create_test_couple("partnerA", "partnerB")

    # Partner B declines consent
    client.cookies.set("session", store.create_session("partnerB"))
    client.post(
        f"/api/couples/{couple_id}/consent", json={"scope": "couple_compute", "granted": False}
    )

    # Check notification outbox
    outbox = store.get_notifications_for_user("partnerA")
    assert len(outbox) == 0  # No notification generated on partner decline


def test_consent_check_timing_and_work_equivalence() -> None:
    """Consent checks must evaluate both partner consent states to completion before combining.

    Both partner branches must execute (no early return on partner A's decline),
    ensuring constant execution work to prevent timing side channels.
    """
    from consent.service import evaluate_couple_consent
    from db.memory_store import ConsentState

    # Scenario 1: Partner A declined, Partner B granted
    work_counter_1 = [0]

    def check_a1():
        work_counter_1[0] += 1
        return ConsentState.declined

    def check_b1():
        work_counter_1[0] += 1
        return ConsentState.granted

    res1 = evaluate_couple_consent(check_a1, check_b1)
    assert res1.can_compute is False
    assert work_counter_1[0] == 2  # Both checks executed!

    # Scenario 2: Partner A granted, Partner B declined
    work_counter_2 = [0]

    def check_a2():
        work_counter_2[0] += 1
        return ConsentState.granted

    def check_b2():
        work_counter_2[0] += 1
        return ConsentState.declined

    res2 = evaluate_couple_consent(check_a2, check_b2)
    assert res2.can_compute is False
    assert work_counter_2[0] == 2  # Both checks executed!
