"""
Subscription & Lifecycle Operations API Endpoints (Tasks 7 & 8).
Handles subscription creation, plan switching, pausing, resuming, cancelling,
and audit logs.
"""

from datetime import timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Subscription,
    Customer,
    Plan,
    BillingCycle,
    BillingCycleStatus,
    AuditLog,
    SubscriptionStatus,
    utc_now,
)
from app.schemas import (
    SubscriptionResponse,
    CreateSubscriptionRequest,
    SubscriptionPlanChangeRequest,
    SubscriptionTransitionRequest,
    SubscriptionTransitionsResponse,
    AuditLogResponse,
)
from app.auth import get_optional_user
from app.services.state_machine import (
    transition_subscription,
    get_allowed_transitions,
    validate_transition,
    ALLOWED_TRANSITIONS,
)

router = APIRouter(tags=["Subscriptions"])


def _build_sub_response(sub: Subscription) -> SubscriptionResponse:
    """Helper to populate plan details and allowed state machine transitions."""
    resp = SubscriptionResponse.model_validate(sub)
    resp.plan_name = sub.plan.name if sub.plan else None
    resp.plan_price = sub.plan.price if sub.plan else None
    resp.allowed_transitions = get_allowed_transitions(sub.status)
    return resp


@router.get(
    "",
    response_model=List[SubscriptionResponse],
    summary="List subscriptions",
)
def list_subscriptions(
    customer_id: Optional[int] = Query(None, description="Filter subscriptions by customer ID"),
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Retrieves subscriptions:
    - If customer_id query param is passed, filters by that customer.
    - If authenticated as a customer without customer_id param, defaults to current_user's subscriptions.
    - Otherwise returns all subscriptions.
    """
    query = db.query(Subscription)

    if customer_id is not None:
        query = query.filter(Subscription.customer_id == customer_id)
    elif current_user is not None and current_user.role != "admin":
        query = query.filter(Subscription.customer_id == current_user.id)

    subs = query.order_by(Subscription.id.desc()).all()
    return [_build_sub_response(s) for s in subs]


@router.get(
    "/{id}",
    response_model=SubscriptionResponse,
    summary="Get subscription by ID",
)
def get_subscription(
    id: int,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Retrieves a single subscription by its primary key ID."""
    sub = db.query(Subscription).filter(Subscription.id == id).first()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription with ID {id} not found.",
        )

    if current_user and current_user.role != "admin" and sub.customer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this subscription.",
        )

    return _build_sub_response(sub)


@router.post(
    "",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new subscription (Task 7)",
)
def create_subscription(
    payload: CreateSubscriptionRequest,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Task 7: Subscription Creation API
    1. Validate customer exists (404 if missing).
    2. Validate plan exists (404 if missing) and is not archived (400 if archived).
    3. Calculate start_date & assign trial period or activate immediately:
       - If plan.trial_days > 0 and not start_immediately_active:
         status = 'trial', trial_end_date = start_date + trial_days, end_date = trial_end_date.
       - Else:
         status = 'active', activated_at = start_date, end_date = +30d (monthly) or +365d (yearly).
    4. Create initial BillingCycle record (pending).
    5. Write AuditLog entry (action="CREATE").
    6. Return complete subscription details.
    """
    # 1. Determine and validate customer
    target_customer_id = payload.customer_id
    if target_customer_id is None:
        if current_user:
            target_customer_id = current_user.id
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="customer_id is required in request body or via Bearer authorization.",
            )

    customer = db.query(Customer).filter(Customer.id == target_customer_id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer with ID {target_customer_id} not found.",
        )

    # 2. Validate plan exists and not archived
    plan = db.query(Plan).filter(Plan.id == payload.plan_id).first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan with ID {payload.plan_id} not found.",
        )

    if plan.is_archived:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot subscribe to an archived plan. Please choose an active plan.",
        )

    # 3. Calculate dates & trial/active status
    start_date = utc_now()
    if plan.trial_days > 0 and not payload.start_immediately_active:
        sub_status = SubscriptionStatus.TRIAL.value
        trial_end = start_date + timedelta(days=plan.trial_days)
        end_date = trial_end
        activated_at = None
    else:
        sub_status = SubscriptionStatus.ACTIVE.value
        trial_end = None
        activated_at = start_date
        interval_days = 365 if plan.billing_interval == "yearly" else 30
        end_date = start_date + timedelta(days=interval_days)

    new_sub = Subscription(
        customer_id=customer.id,
        plan_id=plan.id,
        status=sub_status,
        start_date=start_date,
        end_date=end_date,
        trial_end_date=trial_end,
        activated_at=activated_at,
        status_changed_at=start_date,
        created_at=start_date,
        updated_at=start_date,
    )
    db.add(new_sub)
    db.commit()
    db.refresh(new_sub)

    # 4. Create initial BillingCycle record
    initial_cycle = BillingCycle(
        subscription_id=new_sub.id,
        cycle_start_date=start_date,
        cycle_end_date=end_date,
        renewal_date=end_date,
        status=BillingCycleStatus.PENDING.value,
        created_at=start_date,
        updated_at=start_date,
    )
    db.add(initial_cycle)

    # 5. Write AuditLog
    performer = current_user.email if current_user else customer.email
    audit = AuditLog(
        entity_type="subscription",
        entity_id=new_sub.id,
        action="CREATE",
        old_value=None,
        new_value={
            "status": new_sub.status,
            "plan_id": plan.id,
            "customer_id": customer.id,
            "plan_name": plan.name,
            "billing_interval": plan.billing_interval,
        },
        performed_by=performer,
        created_at=start_date,
    )
    db.add(audit)
    db.commit()
    db.refresh(new_sub)

    return _build_sub_response(new_sub)


# ==========================================
# Task 8: Subscription Lifecycle Operations
# ==========================================

@router.post(
    "/{id}/change-plan",
    response_model=SubscriptionResponse,
    summary="Change plan for an active subscription (Task 8)",
)
def change_subscription_plan(
    id: int,
    payload: SubscriptionPlanChangeRequest,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Task 8.1: Change Plan
    - Validates subscription exists and is not cancelled.
    - Validates target plan exists and is not archived.
    - Updates subscription.plan_id and recalculates end_date if active.
    - Writes AuditLog with action="CHANGE_PLAN".
    """
    sub = db.query(Subscription).filter(Subscription.id == id).first()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription with ID {id} not found.",
        )

    if sub.status == SubscriptionStatus.CANCELLED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change plan of a cancelled subscription.",
        )

    new_plan = db.query(Plan).filter(Plan.id == payload.new_plan_id).first()
    if not new_plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan with ID {payload.new_plan_id} not found.",
        )

    if new_plan.is_archived:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot switch to an archived plan. Please choose an active plan.",
        )

    old_plan_id = sub.plan_id
    sub.plan_id = new_plan.id
    now = utc_now()
    sub.updated_at = now

    # Recalculate end_date if active
    if sub.status == SubscriptionStatus.ACTIVE.value:
        duration = 365 if new_plan.billing_interval == "yearly" else 30
        sub.end_date = now + timedelta(days=duration)

    performer = current_user.email if current_user else "system"
    audit = AuditLog(
        entity_type="subscription",
        entity_id=sub.id,
        action="CHANGE_PLAN",
        old_value=str(old_plan_id),
        new_value=str(new_plan.id),
        performed_by=performer,
        created_at=now,
    )
    db.add(audit)
    db.commit()
    db.refresh(sub)

    return _build_sub_response(sub)


@router.post(
    "/{id}/pause",
    response_model=SubscriptionResponse,
    summary="Pause a subscription (Task 8)",
)
def pause_subscription(
    id: int,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Task 8.2: Pause Subscription
    - Validates state machine transition to 'paused' (allowed from 'trial' or 'active').
    - Rejects if already 'paused' or 'cancelled' with HTTP 400.
    - Sets paused_at and status_changed_at timestamps.
    - Writes AuditLog with action="PAUSE_SUBSCRIPTION".
    """
    sub = db.query(Subscription).filter(Subscription.id == id).first()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription with ID {id} not found.",
        )

    if sub.status == SubscriptionStatus.PAUSED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subscription is already paused.",
        )

    if sub.status == SubscriptionStatus.CANCELLED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot pause a cancelled subscription.",
        )

    # Validate with state machine
    validate_transition(sub.status, SubscriptionStatus.PAUSED.value)

    old_status = sub.status
    now = utc_now()
    sub.status = SubscriptionStatus.PAUSED.value
    sub.paused_at = now
    sub.status_changed_at = now
    sub.updated_at = now

    performer = current_user.email if current_user else "system"
    audit = AuditLog(
        entity_type="subscription",
        entity_id=sub.id,
        action="PAUSE_SUBSCRIPTION",
        old_value={"status": old_status},
        new_value={"status": sub.status},
        performed_by=performer,
        created_at=now,
    )
    db.add(audit)
    db.commit()
    db.refresh(sub)

    return _build_sub_response(sub)


@router.post(
    "/{id}/resume",
    response_model=SubscriptionResponse,
    summary="Resume a paused subscription (Task 8)",
)
def resume_subscription(
    id: int,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Task 8.3: Resume Subscription
    - Validates current status is 'paused' (raises 400 if not paused).
    - Transitions to 'active', updates activated_at and status_changed_at timestamps.
    - Writes AuditLog with action="RESUME_SUBSCRIPTION".
    """
    sub = db.query(Subscription).filter(Subscription.id == id).first()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription with ID {id} not found.",
        )

    if sub.status != SubscriptionStatus.PAUSED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot resume subscription with status '{sub.status}'. Only paused subscriptions can be resumed.",
        )

    validate_transition(sub.status, SubscriptionStatus.ACTIVE.value)

    old_status = sub.status
    now = utc_now()
    sub.status = SubscriptionStatus.ACTIVE.value
    sub.activated_at = now
    sub.status_changed_at = now
    sub.updated_at = now

    performer = current_user.email if current_user else "system"
    audit = AuditLog(
        entity_type="subscription",
        entity_id=sub.id,
        action="RESUME_SUBSCRIPTION",
        old_value={"status": old_status},
        new_value={"status": sub.status},
        performed_by=performer,
        created_at=now,
    )
    db.add(audit)
    db.commit()
    db.refresh(sub)

    return _build_sub_response(sub)


@router.post(
    "/{id}/cancel",
    response_model=SubscriptionResponse,
    summary="Cancel a subscription (Task 8)",
)
def cancel_subscription(
    id: int,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Task 8.4: Cancel Subscription
    - Validates state machine transition to 'cancelled' (rejects if already cancelled with 400).
    - Sets end_date, cancelled_at, and status_changed_at.
    - Marks any pending BillingCycle records as 'completed'.
    - Writes AuditLog with action="CANCEL_SUBSCRIPTION".
    """
    sub = db.query(Subscription).filter(Subscription.id == id).first()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription with ID {id} not found.",
        )

    if sub.status == SubscriptionStatus.CANCELLED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subscription is already cancelled.",
        )

    validate_transition(sub.status, SubscriptionStatus.CANCELLED.value)

    old_status = sub.status
    now = utc_now()
    sub.status = SubscriptionStatus.CANCELLED.value
    sub.cancelled_at = now
    sub.end_date = now
    sub.status_changed_at = now
    sub.updated_at = now

    # Complete pending billing cycles
    pending_cycles = (
        db.query(BillingCycle)
        .filter(
            BillingCycle.subscription_id == sub.id,
            BillingCycle.status == BillingCycleStatus.PENDING.value,
        )
        .all()
    )
    for cycle in pending_cycles:
        cycle.status = BillingCycleStatus.COMPLETED.value
        cycle.updated_at = now

    performer = current_user.email if current_user else "system"
    audit = AuditLog(
        entity_type="subscription",
        entity_id=sub.id,
        action="CANCEL_SUBSCRIPTION",
        old_value={"status": old_status},
        new_value={"status": sub.status},
        performed_by=performer,
        created_at=now,
    )
    db.add(audit)
    db.commit()
    db.refresh(sub)

    return _build_sub_response(sub)


# ==========================================
# Task 4 & Backward-Compatible Endpoints
# ==========================================

@router.get(
    "/{subscription_id}/transitions",
    response_model=SubscriptionTransitionsResponse,
    summary="Get allowed next status transitions for a subscription",
)
def get_subscription_transitions(
    subscription_id: int,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Returns valid destination states according to the Subscription State Machine."""
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if current_user and current_user.role != "admin" and sub.customer_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this subscription")

    allowed = get_allowed_transitions(sub.status)
    return SubscriptionTransitionsResponse(
        subscription_id=sub.id,
        current_status=sub.status,
        allowed_transitions=allowed,
        is_terminal=(len(allowed) == 0),
    )


@router.post(
    "/{subscription_id}/transition",
    response_model=SubscriptionResponse,
    summary="Transition subscription to a new state",
)
def transition_subscription_endpoint(
    subscription_id: int,
    payload: SubscriptionTransitionRequest,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Generic transition endpoint governing strict state machine transitions."""
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if current_user and current_user.role != "admin" and sub.customer_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to mutate this subscription")

    performer = current_user.email if current_user else "system"
    updated_sub = transition_subscription(
        db=db,
        subscription_id=subscription_id,
        new_status=payload.new_status,
        action="STATUS_CHANGE",
        performed_by=performer,
    )

    return _build_sub_response(updated_sub)


@router.get(
    "/{subscription_id}/audit-logs",
    response_model=List[AuditLogResponse],
    summary="Get audit logs for a subscription",
)
def get_subscription_audit_logs(
    subscription_id: int,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Fetches audit trail for state transitions and actions on this subscription."""
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if current_user and current_user.role != "admin" and sub.customer_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view these audit logs")

    logs = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "subscription", AuditLog.entity_id == subscription_id)
        .order_by(AuditLog.created_at.desc())
        .all()
    )
    return [AuditLogResponse.model_validate(log) for log in logs]
