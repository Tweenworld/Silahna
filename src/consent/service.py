"""Consent evaluation service with constant-work guarantees (FR-C1)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from db.memory_store import ConsentState


@dataclass(frozen=True)
class CoupleConsentEvaluation:
    can_compute: bool
    state: str


def evaluate_couple_consent(
    check_partner_a: Callable[[], ConsentState],
    check_partner_b: Callable[[], ConsentState],
) -> CoupleConsentEvaluation:
    """Always evaluate both partner consent states to completion before combining decisions.

    Never early-exit on the first partner's decline or missing consent,
    ensuring constant execution time and work to eliminate side-channel timing leaks.
    """
    state_a = check_partner_a()
    state_b = check_partner_b()

    # Both must explicitly grant consent
    if state_a == ConsentState.granted and state_b == ConsentState.granted:
        return CoupleConsentEvaluation(can_compute=True, state="granted")

    # If either declined, revoked, or pending, return uniform blocked state
    return CoupleConsentEvaluation(can_compute=False, state="blocked")
