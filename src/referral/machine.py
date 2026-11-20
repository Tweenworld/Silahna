"""Referral transitions. Steps cannot be skipped (FR-R1)."""

from __future__ import annotations

from enum import StrEnum


class ReferralState(StrEnum):
    created = "created"
    assigned = "assigned"
    booked = "booked"
    completed = "completed"
    closed = "closed"


class ReferralAction(StrEnum):
    assign = "assign"
    book = "book"
    complete = "complete"
    close = "close"


class ReferralError(ValueError):
    """The transition is not allowed."""


_NEXT: dict[tuple[ReferralState, ReferralAction], ReferralState] = {
    (ReferralState.created, ReferralAction.assign): ReferralState.assigned,
    (ReferralState.assigned, ReferralAction.book): ReferralState.booked,
    (ReferralState.booked, ReferralAction.complete): ReferralState.completed,
    (ReferralState.completed, ReferralAction.close): ReferralState.closed,
}


def transition(
    state: ReferralState,
    action: ReferralAction,
    *,
    owner_id: str | None,
    evidence: str | None,
    actor_id: str,
    actor_is_supervisor: bool,
) -> tuple[ReferralState, str | None]:
    """Return the next state and the owner after this action."""

    if (state, action) not in _NEXT:
        raise ReferralError("transition skipped or unknown")
    if action is ReferralAction.assign:
        if not owner_id:
            raise ReferralError("assignment needs an owner")
        return _NEXT[(state, action)], owner_id
    if not owner_id:
        raise ReferralError("referral has no owner")
    if actor_id != owner_id and not actor_is_supervisor:
        raise ReferralError("only the owner or a supervisor may advance")
    if action is ReferralAction.close and not (evidence and evidence.strip()):
        raise ReferralError("closure needs documented evidence")
    return _NEXT[(state, action)], owner_id
