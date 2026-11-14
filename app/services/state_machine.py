"""
Subscription Status State Machine.
Defines allowed subscription states, strict state transition validation,
and automated audit logging upon status mutation.
"""

import enum
from typing import Set, Dict, List
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import Subscription, SubscriptionStatus, utc_now
from app.services.audit import log_system_event


class InvalidStateTransitionError(HTTPException):
    """Exception raised when an invalid status transition is attempted."""
    def __init__(self, current_status: str, new_status: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid state transition: Cannot transition subscription from "
                f"'{current_status}' to '{new_status}'. "
                f"Allowed transitions from '{current_status}': "
                f"{list(ALLOWED_TRANSITIONS.get(current_status, [])) or 'None (Terminal state)'}."
            ),
        )


# Strict state transition map
ALLOWED_TRANSITIONS: Dict[str, Set[str]] = {
    SubscriptionStatus.TRIAL.value: {
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.PAUSED.value,
        SubscriptionStatus.CANCELLED.value,
    },
    SubscriptionStatus.ACTIVE.value: {
        SubscriptionStatus.PAST_DUE.value,
        SubscriptionStatus.PAUSED.value,
        SubscriptionStatus.CANCELLED.value,
    },
    SubscriptionStatus.PAUSED.value: {
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.CANCELLED.value,
    },
    SubscriptionStatus.PAST_DUE.value: {
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.CANCELLED.value,
    },
    SubscriptionStatus.CANCELLED.value: set(),  # Terminal state: no outbound transitions
}


def can_transition(current_status: str, new_status: str) -> bool:
    """
    Checks if a transition from current_status to new_status is allowed.
    """
    allowed = ALLOWED_TRANSITIONS.get(current_status, set())
    return new_status in allowed


def get_allowed_transitions(current_status: str) -> List[str]:
    """
    Returns the list of allowed destination states from current_status.
    """
    return sorted(list(ALLOWED_TRANSITIONS.get(current_status, set())))


def validate_transition(current_status: str, new_status: str) -> None:
    """
    Validates the state transition, raising InvalidStateTransitionError if forbidden.
    """
    if not can_transition(current_status, new_status):
        raise InvalidStateTransitionError(current_status, new_status)


def transition_subscription(
    db: Session,
    subscription_id: int,
    new_status: str,
    action: str = "STATUS_CHANGE",
    performed_by: str = "system",
) -> Subscription:
    """
    Executes a status transition for a subscription:
    1. Verifies subscription exists.
    2. Validates the state transition against ALLOWED_TRANSITIONS.
    3. Updates status timestamps (paused_at, activated_at, cancelled_at, status_changed_at).
    4. If new status is 'cancelled', updates end_date and completes pending billing cycles.
    5. Mutates subscription.status.
    6. Writes an immutable AuditLog entry capturing old and new status.
    7. Commits transaction and returns the refreshed Subscription.
    """
    subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription with ID {subscription_id} not found.",
        )

    old_status = subscription.status

    # Validate transition
    validate_transition(old_status, new_status)

    now = utc_now()
    subscription.status = new_status
    subscription.status_changed_at = now
    subscription.updated_at = now

    # Update state-specific timestamps & lifecycle side effects
    if new_status == SubscriptionStatus.PAUSED.value:
        subscription.paused_at = now
    elif new_status == SubscriptionStatus.ACTIVE.value:
        subscription.activated_at = now
    elif new_status == SubscriptionStatus.CANCELLED.value:
        subscription.cancelled_at = now
        subscription.end_date = now
        # Complete any pending billing cycles
        from app.models import BillingCycle, BillingCycleStatus
        pending_cycles = (
            db.query(BillingCycle)
            .filter(
                BillingCycle.subscription_id == subscription.id,
                BillingCycle.status == BillingCycleStatus.PENDING.value,
            )
            .all()
        )
        for cycle in pending_cycles:
            cycle.status = BillingCycleStatus.COMPLETED.value
            cycle.updated_at = now

    # Determine standard action name if generic
    resolved_action = action
    if resolved_action == "STATUS_CHANGE":
        if new_status == SubscriptionStatus.PAUSED.value:
            resolved_action = "SUBSCRIPTION_PAUSED"
        elif new_status == SubscriptionStatus.ACTIVE.value:
            resolved_action = "SUBSCRIPTION_RESUMED"
        elif new_status == SubscriptionStatus.CANCELLED.value:
            resolved_action = "SUBSCRIPTION_CANCELLED"

    db.commit()
    db.refresh(subscription)

    # Write AuditLog via centralized service
    log_system_event(
        db=db,
        entity_type="subscription",
        entity_id=subscription.id,
        action=resolved_action,
        old_value={"status": old_status},
        new_value={"status": new_status},
        performed_by=performed_by,
    )

    return subscription
