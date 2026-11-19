"""
Billing & Invoices API Router.
Handles automated invoice generation from billing cycles, invoice listing with search/status filters,
detailed line-item invoice retrieval, and in-memory PDF invoice downloads via fpdf2.
"""

import io
from datetime import timedelta
from typing import List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.auth import get_optional_user
from app.database import get_db
from app.models import (
    BillingCycle,
    Customer,
    Invoice,
    InvoiceStatus,
    Plan,
    Subscription,
    SubscriptionStatus,
    utc_now,
)
from app.schemas import (
    InvoiceDetailResponse,
    InvoiceGenerationResponse,
    InvoiceLineItem,
    InvoiceListItemResponse,
)
from app.services.audit import log_system_event
from app.services.invoice_pdf import generate_invoice_pdf

router = APIRouter(tags=["Billing & Invoices"])

STANDARD_TAX_RATE = 0.18


def _serialize_invoice_list_item(inv: Invoice) -> InvoiceListItemResponse:
    """Enriches an Invoice ORM model with customer and plan display fields."""
    customer = inv.customer
    sub = inv.subscription
    plan = sub.plan if sub else None

    return InvoiceListItemResponse(
        id=inv.id,
        invoice_number=inv.invoice_number,
        subscription_id=inv.subscription_id,
        customer_id=inv.customer_id,
        billing_cycle_id=inv.billing_cycle_id,
        invoice_date=inv.invoice_date,
        due_date=inv.due_date,
        subtotal=float(inv.subtotal),
        tax_amount=float(inv.tax_amount),
        total_amount=float(inv.total_amount),
        status=inv.status,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
        customer_name=customer.name if customer else None,
        customer_email=customer.email if customer else None,
        customer_company=customer.company_name if customer else None,
        plan_name=plan.name if plan else None,
        billing_interval=plan.billing_interval if plan else None,
    )


def _next_invoice_sequence(db: Session, date_prefix: str) -> int:
    """Determines the next sequential 4-digit counter for a given INV-YYYYMMDD- prefix."""
    pattern = f"{date_prefix}%"
    existing_numbers = (
        db.query(Invoice.invoice_number)
        .filter(Invoice.invoice_number.like(pattern))
        .all()
    )
    max_seq = 0
    for (inv_num,) in existing_numbers:
        suffix = inv_num.replace(date_prefix, "", 1)
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return max_seq + 1


@router.post(
    "/generate",
    response_model=InvoiceGenerationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate invoices for un-invoiced billing cycles",
)
def generate_invoices(
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Scans all pending or active billing cycles that do not yet have an associated invoice:
    - Generates a unique invoice number (INV-YYYYMMDD-XXXX).
    - Calculates subtotal from the associated Plan price, applies 18% standard tax, and computes total_amount.
    - Persists invoices with status='unpaid', records audit events, and returns a generation summary.
    """
    now = utc_now()
    date_prefix = f"INV-{now.strftime('%Y%m%d')}-"
    next_seq = _next_invoice_sequence(db, date_prefix)

    # Fetch all pending or active billing cycles along with subscriptions, plans, and customers
    cycles: List[BillingCycle] = (
        db.query(BillingCycle)
        .options(
            joinedload(BillingCycle.subscription).joinedload(Subscription.plan),
            joinedload(BillingCycle.subscription).joinedload(Subscription.customer),
        )
        .filter(func.lower(BillingCycle.status).in_(["pending", "active"]))
        .order_by(BillingCycle.id.asc())
        .all()
    )

    # Identify cycles already linked to an invoice
    invoiced_cycle_ids: Set[int] = {
        row[0]
        for row in db.query(Invoice.billing_cycle_id)
        .filter(Invoice.billing_cycle_id.isnot(None))
        .all()
    }

    # Fetch legacy invoices without billing_cycle_id to avoid double-invoicing older cycles
    legacy_invoices: List[Invoice] = (
        db.query(Invoice)
        .filter(Invoice.billing_cycle_id.is_(None))
        .all()
    )

    performer = current_user.email if current_user else "billing_engine"
    generated_invoices: List[Invoice] = []
    skipped_count = 0

    for cycle in cycles:
        if cycle.id in invoiced_cycle_ids:
            skipped_count += 1
            continue

        sub = cycle.subscription
        if not sub or sub.status == SubscriptionStatus.CANCELLED.value:
            skipped_count += 1
            continue

        plan = sub.plan or db.query(Plan).filter(Plan.id == sub.plan_id).first()
        if not plan:
            skipped_count += 1
            continue

        # Check legacy invoice date overlap for same subscription
        has_legacy_match = any(
            inv.subscription_id == sub.id
            and cycle.cycle_start_date <= inv.invoice_date <= cycle.cycle_end_date
            for inv in legacy_invoices
        )
        if has_legacy_match:
            skipped_count += 1
            continue

        # Generate unique invoice number INV-YYYYMMDD-XXXX
        invoice_number = f"{date_prefix}{next_seq:04d}"
        next_seq += 1

        subtotal = round(float(plan.price), 2)
        tax_amount = round(subtotal * STANDARD_TAX_RATE, 2)
        total_amount = round(subtotal + tax_amount, 2)

        new_invoice = Invoice(
            invoice_number=invoice_number,
            subscription_id=sub.id,
            customer_id=sub.customer_id,
            billing_cycle_id=cycle.id,
            invoice_date=now,
            due_date=now + timedelta(days=15),
            subtotal=subtotal,
            tax_amount=tax_amount,
            total_amount=total_amount,
            status=InvoiceStatus.UNPAID.value,
            created_at=now,
            updated_at=now,
        )
        db.add(new_invoice)
        db.flush()

        log_system_event(
            db=db,
            entity_type="invoice",
            entity_id=new_invoice.id,
            action="INVOICE_GENERATED",
            old_value=None,
            new_value={
                "invoice_number": new_invoice.invoice_number,
                "subscription_id": sub.id,
                "customer_id": sub.customer_id,
                "billing_cycle_id": cycle.id,
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "total_amount": total_amount,
                "status": new_invoice.status,
            },
            performed_by=performer,
        )

        invoiced_cycle_ids.add(cycle.id)
        generated_invoices.append(new_invoice)

    db.commit()

    # Reload relationships for clean response serialization
    serialized: List[InvoiceListItemResponse] = []
    if generated_invoices:
        gen_ids = [inv.id for inv in generated_invoices]
        reloaded = (
            db.query(Invoice)
            .options(
                joinedload(Invoice.customer),
                joinedload(Invoice.subscription).joinedload(Subscription.plan),
            )
            .filter(Invoice.id.in_(gen_ids))
            .order_by(Invoice.id.asc())
            .all()
        )
        serialized = [_serialize_invoice_list_item(inv) for inv in reloaded]

    return InvoiceGenerationResponse(
        generated_count=len(serialized),
        skipped_count=skipped_count,
        invoices=serialized,
    )


@router.get(
    "",
    response_model=List[InvoiceListItemResponse],
    summary="List invoices with search and status filters",
)
def list_invoices(
    search: Optional[str] = Query(
        None,
        description="Filter by invoice number or partial invoice date (e.g. 'INV-2026' or '2026-11-18')",
    ),
    status_filter: Optional[str] = Query(
        None,
        alias="status",
        description="Exact invoice status match ('paid' or 'unpaid')",
    ),
    db: Session = Depends(get_db),
):
    """
    Returns billing invoices enriched with customer and plan details.
    Supports dynamic filtering by invoice number, partial invoice date, and exact payment status.
    """
    query = (
        db.query(Invoice)
        .options(
            joinedload(Invoice.customer),
            joinedload(Invoice.subscription).joinedload(Subscription.plan),
        )
    )

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Invoice.invoice_number.ilike(term),
                func.to_char(Invoice.invoice_date, "YYYY-MM-DD").ilike(term),
            )
        )

    if status_filter and status_filter.strip() and status_filter.strip().lower() != "all":
        normalized_status = status_filter.strip().lower()
        query = query.filter(func.lower(Invoice.status) == normalized_status)

    invoices = query.order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).all()
    return [_serialize_invoice_list_item(inv) for inv in invoices]


@router.get(
    "/{id}",
    response_model=InvoiceDetailResponse,
    summary="Get detailed invoice with customer and plan line items",
)
def get_invoice_detail(
    id: int,
    db: Session = Depends(get_db),
):
    """
    Retrieves complete invoice details joined with Customer, Subscription, Plan, and BillingCycle data.
    """
    inv = (
        db.query(Invoice)
        .options(
            joinedload(Invoice.customer),
            joinedload(Invoice.subscription).joinedload(Subscription.plan),
            joinedload(Invoice.billing_cycle),
        )
        .filter(Invoice.id == id)
        .first()
    )
    if not inv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice with ID {id} not found.",
        )

    customer = inv.customer
    sub = inv.subscription
    plan = sub.plan if sub else None
    cycle = inv.billing_cycle

    plan_name = plan.name if plan else "Subscription Plan"
    billing_interval = plan.billing_interval if plan else "monthly"

    line_items = [
        InvoiceLineItem(
            description=f"{plan_name} ({billing_interval.capitalize()} Recurring Subscription)",
            plan_name=plan_name,
            billing_interval=billing_interval,
            quantity=1,
            unit_price=round(float(inv.subtotal), 2),
            amount=round(float(inv.subtotal), 2),
        )
    ]

    return InvoiceDetailResponse(
        id=inv.id,
        invoice_number=inv.invoice_number,
        subscription_id=inv.subscription_id,
        customer_id=inv.customer_id,
        billing_cycle_id=inv.billing_cycle_id,
        invoice_date=inv.invoice_date,
        due_date=inv.due_date,
        subtotal=float(inv.subtotal),
        tax_amount=float(inv.tax_amount),
        total_amount=float(inv.total_amount),
        status=inv.status,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
        customer_name=customer.name if customer else None,
        customer_email=customer.email if customer else None,
        customer_company=customer.company_name if customer else None,
        plan_name=plan_name,
        billing_interval=billing_interval,
        subscription_status=sub.status if sub else None,
        cycle_start_date=cycle.cycle_start_date if cycle else None,
        cycle_end_date=cycle.cycle_end_date if cycle else None,
        tax_rate=STANDARD_TAX_RATE,
        line_items=line_items,
    )


@router.get(
    "/{id}/pdf",
    summary="Download formatted PDF invoice",
    response_class=StreamingResponse,
)
def download_invoice_pdf(
    id: int,
    db: Session = Depends(get_db),
):
    """
    Constructs a formatted PDF invoice in memory using fpdf2 and streams it as a downloadable PDF attachment.
    """
    inv = (
        db.query(Invoice)
        .options(
            joinedload(Invoice.customer),
            joinedload(Invoice.subscription).joinedload(Subscription.plan),
            joinedload(Invoice.billing_cycle),
        )
        .filter(Invoice.id == id)
        .first()
    )
    if not inv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice with ID {id} not found.",
        )

    customer = inv.customer
    sub = inv.subscription
    plan = sub.plan if sub else None
    cycle = inv.billing_cycle

    pdf_bytes = generate_invoice_pdf(
        invoice_number=inv.invoice_number,
        status=inv.status,
        invoice_date=inv.invoice_date,
        due_date=inv.due_date,
        customer_name=customer.name if customer else "Valued Customer",
        customer_email=customer.email if customer else "N/A",
        customer_company=customer.company_name if customer else None,
        customer_id=inv.customer_id,
        subscription_id=inv.subscription_id,
        plan_name=plan.name if plan else "Subscription Plan",
        billing_interval=plan.billing_interval if plan else "monthly",
        subtotal=float(inv.subtotal),
        tax_amount=float(inv.tax_amount),
        total_amount=float(inv.total_amount),
        cycle_start_date=cycle.cycle_start_date if cycle else None,
        cycle_end_date=cycle.cycle_end_date if cycle else None,
    )

    filename = f"{inv.invoice_number}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
