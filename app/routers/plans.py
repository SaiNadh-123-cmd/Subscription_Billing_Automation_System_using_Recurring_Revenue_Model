"""
Plan Management API Endpoints (Task 5).
Handles plan creation, listing, updating, archiving, and safe deletion with audit logging.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Plan, Subscription, AuditLog, utc_now
from app.schemas import PlanCreate, PlanUpdate, PlanResponse
from app.auth import get_optional_user, Customer

router = APIRouter(tags=["Plan Management"])


@router.post(
    "",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new billing plan",
)
def create_plan(
    payload: PlanCreate,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Creates a new subscription plan:
    - Checks for duplicate plan name (case-insensitive) -> 400 Bad Request
    - Persists Plan record
    - Writes an immutable AuditLog entry
    - Returns HTTP 201 Created
    """
    existing_plan = (
        db.query(Plan)
        .filter(Plan.name.ilike(payload.name.strip()))
        .first()
    )
    if existing_plan:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Plan with name '{payload.name.strip()}' already exists.",
        )

    now = utc_now()
    plan = Plan(
        name=payload.name.strip(),
        price=payload.price,
        billing_interval=payload.billing_interval.strip().lower(),
        trial_days=payload.trial_days,
        features=payload.features,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    # Audit Log
    performer = current_user.email if current_user else "admin"
    audit_entry = AuditLog(
        entity_type="plan",
        entity_id=plan.id,
        action="CREATE",
        old_value=None,
        new_value={
            "name": plan.name,
            "price": plan.price,
            "billing_interval": plan.billing_interval,
            "trial_days": plan.trial_days,
            "features": plan.features,
        },
        performed_by=performer,
        created_at=now,
    )
    db.add(audit_entry)
    db.commit()

    return plan


@router.get(
    "",
    response_model=List[PlanResponse],
    summary="List all plans",
)
def list_plans(
    include_archived: bool = Query(False, description="Whether to include archived plans"),
    db: Session = Depends(get_db),
):
    """
    Lists subscription plans. By default, returns only non-archived plans.
    """
    query = db.query(Plan)
    if not include_archived:
        query = query.filter(Plan.is_archived.is_(False))

    plans = query.order_by(Plan.id.asc()).all()
    return plans


@router.get(
    "/{id}",
    response_model=PlanResponse,
    summary="Get plan by ID",
)
def get_plan(
    id: int,
    db: Session = Depends(get_db),
):
    """Retrieves a single plan by its ID."""
    plan = db.query(Plan).filter(Plan.id == id).first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan with ID {id} not found.",
        )
    return plan


@router.put(
    "/{id}",
    response_model=PlanResponse,
    summary="Update an existing plan",
)
def update_plan(
    id: int,
    payload: PlanUpdate,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Updates plan attributes and logs modifications in AuditLog.
    """
    plan = db.query(Plan).filter(Plan.id == id).first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan with ID {id} not found.",
        )

    # Check duplicate name if name is changing
    if payload.name and payload.name.strip().lower() != plan.name.lower():
        name_conflict = (
            db.query(Plan)
            .filter(Plan.name.ilike(payload.name.strip()), Plan.id != id)
            .first()
        )
        if name_conflict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Another plan with name '{payload.name.strip()}' already exists.",
            )

    old_data = {
        "name": plan.name,
        "price": plan.price,
        "billing_interval": plan.billing_interval,
        "trial_days": plan.trial_days,
        "features": plan.features,
    }

    if payload.name is not None:
        plan.name = payload.name.strip()
    if payload.price is not None:
        plan.price = payload.price
    if payload.billing_interval is not None:
        plan.billing_interval = payload.billing_interval.strip().lower()
    if payload.trial_days is not None:
        plan.trial_days = payload.trial_days
    if payload.features is not None:
        plan.features = payload.features

    now = utc_now()
    plan.updated_at = now

    new_data = {
        "name": plan.name,
        "price": plan.price,
        "billing_interval": plan.billing_interval,
        "trial_days": plan.trial_days,
        "features": plan.features,
    }

    # Audit Log
    performer = current_user.email if current_user else "admin"
    audit_entry = AuditLog(
        entity_type="plan",
        entity_id=plan.id,
        action="UPDATE",
        old_value=old_data,
        new_value=new_data,
        performed_by=performer,
        created_at=now,
    )
    db.add(audit_entry)

    db.commit()
    db.refresh(plan)
    return plan


@router.patch(
    "/{id}/archive",
    response_model=PlanResponse,
    summary="Archive a plan",
)
def archive_plan(
    id: int,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Archives a plan so it cannot be selected for new subscriptions,
    while leaving existing active subscriptions intact.
    """
    plan = db.query(Plan).filter(Plan.id == id).first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan with ID {id} not found.",
        )

    now = utc_now()
    plan.is_archived = True
    plan.updated_at = now

    performer = current_user.email if current_user else "admin"
    audit_entry = AuditLog(
        entity_type="plan",
        entity_id=plan.id,
        action="ARCHIVE",
        old_value={"is_archived": False},
        new_value={"is_archived": True},
        performed_by=performer,
        created_at=now,
    )
    db.add(audit_entry)

    db.commit()
    db.refresh(plan)
    return plan


@router.delete(
    "/{id}",
    summary="Delete an unused plan",
)
def delete_plan(
    id: int,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Deletes an unused plan. If active or existing subscriptions depend on this plan,
    raises HTTP 400 advising to archive instead.
    """
    plan = db.query(Plan).filter(Plan.id == id).first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan with ID {id} not found.",
        )

    # Check for active subscriptions
    active_subs = (
        db.query(Subscription)
        .filter(
            Subscription.plan_id == id,
            Subscription.status.in_(["trial", "active", "past_due", "paused"]),
        )
        .count()
    )
    if active_subs > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete plan with active subscriptions. Please archive the plan instead.",
        )

    # Check for any historical subscriptions
    any_subs = db.query(Subscription).filter(Subscription.plan_id == id).count()
    if any_subs > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete plan with existing subscription records. Please archive the plan instead.",
        )

    plan_name = plan.name
    now = utc_now()
    performer = current_user.email if current_user else "admin"

    db.delete(plan)

    audit_entry = AuditLog(
        entity_type="plan",
        entity_id=id,
        action="DELETE",
        old_value={"name": plan_name},
        new_value=None,
        performed_by=performer,
        created_at=now,
    )
    db.add(audit_entry)

    db.commit()
    return {"detail": "Plan deleted successfully", "id": id}
