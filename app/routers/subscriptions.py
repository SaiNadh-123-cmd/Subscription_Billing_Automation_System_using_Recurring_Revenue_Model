"""
Subscription & Lifecycle Operations API Endpoints.
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
    ProrationAdjustment,
    Invoice,
    SubscriptionStatus,
    utc_now,
)
from app.schemas import (
    SubscriptionResponse,
    CreateSubscriptionRequest,
    SubscriptionPlanChangeRequest,
    SubscriptionPlanChangeResponse,
    ProrationPreviewResponse,
    ProrationAdjustmentResponse,
    PlanChangeHistoryItem,
    SubscriptionTransitionRequest,
    SubscriptionTransitionsResponse,
    AuditLogResponse,
)
from app.auth import get_optional_user
from app.services.state_machine import (
    transition_subscription,
    get_allowed_transitions,
    validate_transition,
)
from app.services.audit import log_system_event
from app.services.proration import ProrationService

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
    summary="Create a new subscription",
)
def create_subscription(
    payload: CreateSubscriptionRequest,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Subscription Creation Endpoint:
    1. Validate customer exists (404 if missing).
    2. Validate plan exists (404 if missing) and is not archived (400 if archived).
    3. Calculate start_date & assign trial period or activate immediately:
       - If plan.trial_days > 0 and not start_immediately_active:
         status = 'trial', trial_end_date = start_date + trial_days, end_date = trial_end_date.
       - Else:
         status = 'active', activated_at = start_date, end_date = +30d (monthly) or +365d (yearly).
    4. Create initial BillingCycle record (pending).
    5. Write AuditLog entry (action="SUBSCRIPTION_CREATED").
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
    db.commit()

    # 5. Centralized Audit Log
    performer = current_user.email if current_user else customer.email
    log_system_event(
        db=db,
        entity_type="subscription",
        entity_id=new_sub.id,
        action="SUBSCRIPTION_CREATED",
        old_value=None,
        new_value={
            "status": new_sub.status,
            "plan_id": plan.id,
            "customer_id": customer.id,
            "plan_name": plan.name,
            "billing_interval": plan.billing_interval,
        },
        performed_by=performer,
    )
    db.refresh(new_sub)

    return _build_sub_response(new_sub)


# ==========================================
# Subscription Lifecycle Operations
# ==========================================

@router.get(
    "/{id}/proration-preview",
    response_model=ProrationPreviewResponse,
    summary="Preview proration credit, charge, and amount due for a plan change",
)
def preview_plan_change_proration(
    id: int,
    new_plan_id: int = Query(..., description="Target plan ID to preview"),
    db: Session = Depends(get_db),
):
    """
    Calculates fair mid-cycle proration preview without committing changes:
    - Calculates remaining days in current billing cycle
    - Calculates unused current plan credit
    - Calculates new plan charge for remaining days
    - Computes net amount due
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
            detail="Cannot preview plan change for a cancelled subscription.",
        )

    new_plan = db.query(Plan).filter(Plan.id == new_plan_id).first()
    if not new_plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan with ID {new_plan_id} not found.",
        )

    if new_plan.is_archived:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot switch to an archived plan. Please choose an active plan.",
        )

    calc = ProrationService.calculate_proration(sub=sub, new_plan=new_plan, db=db)
    return ProrationPreviewResponse(**calc)


@router.post(
    "/{id}/change-plan",
    response_model=SubscriptionPlanChangeResponse,
    summary="Change plan for an active subscription with fair proration",
)
def change_subscription_plan(
    id: int,
    payload: SubscriptionPlanChangeRequest,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Plan Change Operation with Proration:
    - Validates subscription exists and is not cancelled.
    - Validates target plan exists and is not archived.
    - Calculates unused credit, remaining charge, and net amount due.
    - Generates adjustment invoice if net amount due > 0.
    - Records ProrationAdjustment and AuditLog (action="PLAN_CHANGED").
    - Updates subscription.plan_id and returns updated subscription with proration details.
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

    performer = current_user.email if current_user else "system"
    updated_sub, adjustment, inv = ProrationService.execute_plan_change(
        db=db,
        sub=sub,
        new_plan=new_plan,
        performer=performer,
    )

    base_resp = _build_sub_response(updated_sub)

    adj_resp = None
    if adjustment:
        adj_resp = ProrationAdjustmentResponse(
            id=adjustment.id,
            subscription_id=adjustment.subscription_id,
            customer_id=adjustment.customer_id,
            old_plan_id=adjustment.old_plan_id,
            new_plan_id=adjustment.new_plan_id,
            old_plan_name=adjustment.old_plan_name,
            new_plan_name=adjustment.new_plan_name,
            remaining_days=adjustment.remaining_days,
            total_days=adjustment.total_days,
            unused_credit=adjustment.unused_credit,
            new_plan_charge=adjustment.new_plan_charge,
            net_amount_due=adjustment.net_amount_due,
            invoice_id=inv.id if inv else None,
            invoice_number=inv.invoice_number if inv else None,
            created_at=adjustment.created_at,
        )

    return SubscriptionPlanChangeResponse(
        id=base_resp.id,
        customer_id=base_resp.customer_id,
        plan_id=base_resp.plan_id,
        status=base_resp.status,
        start_date=base_resp.start_date,
        end_date=base_resp.end_date,
        trial_end_date=base_resp.trial_end_date,
        paused_at=base_resp.paused_at,
        activated_at=base_resp.activated_at,
        cancelled_at=base_resp.cancelled_at,
        status_changed_at=base_resp.status_changed_at,
        created_at=base_resp.created_at,
        updated_at=base_resp.updated_at,
        plan_name=base_resp.plan_name,
        plan_price=base_resp.plan_price,
        allowed_transitions=base_resp.allowed_transitions,
        proration=adj_resp,
        invoice_id=inv.id if inv else None,
        invoice_number=inv.invoice_number if inv else None,
    )


@router.get(
    "/{id}/plan-history",
    response_model=List[PlanChangeHistoryItem],
    summary="Get plan change and proration history for a subscription",
)
def get_plan_change_history(
    id: int,
    db: Session = Depends(get_db),
):
    """
    Returns the historical plan changes and proration adjustments for this subscription.
    """
    sub = db.query(Subscription).filter(Subscription.id == id).first()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription with ID {id} not found.",
        )

    adjustments = (
        db.query(ProrationAdjustment)
        .filter(ProrationAdjustment.subscription_id == id)
        .order_by(ProrationAdjustment.created_at.desc())
        .all()
    )

    items = []
    for a in adjustments:
        inv_num = a.invoice.invoice_number if a.invoice else None
        dt_str = a.created_at.strftime("%d %b %Y") if a.created_at else None
        items.append(
            PlanChangeHistoryItem(
                id=a.id,
                old_plan_name=a.old_plan_name,
                new_plan_name=a.new_plan_name,
                remaining_days=a.remaining_days,
                unused_credit=a.unused_credit,
                new_plan_charge=a.new_plan_charge,
                net_amount_due=a.net_amount_due,
                invoice_id=a.invoice_id,
                invoice_number=inv_num,
                created_at=a.created_at,
                formatted_date=dt_str,
            )
        )
    return items


@router.post(
    "/{id}/pause",
    response_model=SubscriptionResponse,
    summary="Pause an active or trial subscription",
)
def pause_subscription(
    id: int,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Pause Subscription Operation:
    - Validates state machine transition to 'paused' (allowed from 'trial' or 'active').
    - Rejects if already 'paused' or 'cancelled' with HTTP 400.
    - Sets paused_at and status_changed_at timestamps.
    - Writes AuditLog with action="SUBSCRIPTION_PAUSED".
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

    validate_transition(sub.status, SubscriptionStatus.PAUSED.value)

    old_status = sub.status
    now = utc_now()
    sub.status = SubscriptionStatus.PAUSED.value
    sub.paused_at = now
    sub.status_changed_at = now
    sub.updated_at = now

    db.commit()
    db.refresh(sub)

    performer = current_user.email if current_user else "system"
    log_system_event(
        db=db,
        entity_type="subscription",
        entity_id=sub.id,
        action="SUBSCRIPTION_PAUSED",
        old_value={"status": old_status},
        new_value={"status": sub.status},
        performed_by=performer,
    )

    return _build_sub_response(sub)


@router.post(
    "/{id}/resume",
    response_model=SubscriptionResponse,
    summary="Resume a paused subscription",
)
def resume_subscription(
    id: int,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Resume Subscription Operation:
    - Validates current status is 'paused' (raises 400 if not paused).
    - Transitions to 'active', updates activated_at and status_changed_at timestamps.
    - Writes AuditLog with action="SUBSCRIPTION_RESUMED".
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

    db.commit()
    db.refresh(sub)

    performer = current_user.email if current_user else "system"
    log_system_event(
        db=db,
        entity_type="subscription",
        entity_id=sub.id,
        action="SUBSCRIPTION_RESUMED",
        old_value={"status": old_status},
        new_value={"status": sub.status},
        performed_by=performer,
    )

    return _build_sub_response(sub)


@router.post(
    "/{id}/cancel",
    response_model=SubscriptionResponse,
    summary="Cancel a subscription",
)
def cancel_subscription(
    id: int,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Cancel Subscription Operation:
    - Validates state machine transition to 'cancelled' (rejects if already cancelled with 400).
    - Sets end_date, cancelled_at, and status_changed_at.
    - Marks any pending BillingCycle records as 'completed'.
    - Writes AuditLog with action="SUBSCRIPTION_CANCELLED".
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

    db.commit()
    db.refresh(sub)

    performer = current_user.email if current_user else "system"
    log_system_event(
        db=db,
        entity_type="subscription",
        entity_id=sub.id,
        action="SUBSCRIPTION_CANCELLED",
        old_value={"status": old_status},
        new_value={"status": sub.status},
        performed_by=performer,
    )

    return _build_sub_response(sub)


# ==========================================
# State Machine & Transition Inquiries
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
