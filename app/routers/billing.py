"""
Billing Cycle Engine & Invoicing Router.
Provides endpoints for automated recurring renewals, invoice tracking, and cycle management.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BillingCycle, Invoice, Customer
from app.schemas import (
    BillingEngineTriggerResponse,
    BillingCycleResponse,
    InvoiceResponse,
)
from app.services.billing_engine import BillingService
from app.auth import get_optional_user

router = APIRouter(tags=["Billing"])


@router.post(
    "/trigger-engine",
    response_model=BillingEngineTriggerResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger recurring billing renewal engine",
)
def trigger_billing_engine(
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Executes automated recurring billing cycle renewals:
    - Scans for active subscriptions whose latest billing cycle is due.
    - Completes previous cycles and rolls forward next renewal interval.
    - Automatically issues draft financial invoices with standard tax.
    - Records immutable audit trail events.
    """
    performer = current_user.email if current_user else "billing_engine"
    result = BillingService.process_renewals(db=db, performed_by=performer)
    return BillingEngineTriggerResponse(
        processed_subscriptions=result["processed_subscriptions"],
        new_invoices_created=result["new_invoices_created"],
        details=result.get("details", []),
    )


@router.get(
    "/cycles",
    response_model=List[BillingCycleResponse],
    summary="List billing cycle windows",
)
def list_billing_cycles(
    subscription_id: Optional[int] = Query(None, description="Filter by subscription ID"),
    db: Session = Depends(get_db),
):
    """Retrieves billing cycle windows sorted by creation date."""
    query = db.query(BillingCycle)
    if subscription_id is not None:
        query = query.filter(BillingCycle.subscription_id == subscription_id)
    return query.order_by(BillingCycle.cycle_end_date.desc()).all()


@router.get(
    "/invoices",
    response_model=List[InvoiceResponse],
    summary="List billing invoices",
)
def list_invoices(
    customer_id: Optional[int] = Query(None, description="Filter by customer ID"),
    subscription_id: Optional[int] = Query(None, description="Filter by subscription ID"),
    db: Session = Depends(get_db),
):
    """Retrieves generated invoices sorted by invoice date."""
    query = db.query(Invoice)
    if customer_id is not None:
        query = query.filter(Invoice.customer_id == customer_id)
    if subscription_id is not None:
        query = query.filter(Invoice.subscription_id == subscription_id)
    return query.order_by(Invoice.invoice_date.desc()).all()
