"""
Customer Management API Endpoints.
Handles customer creation, retrieval with subscription details, listing, and profile updates.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Customer, utc_now
from app.schemas import CustomerCreate, CustomerUpdate, CustomerResponse, CustomerDetailResponse, SubscriptionResponse
from app.auth import hash_password, get_optional_user
from app.services.state_machine import get_allowed_transitions
from app.services.audit import log_system_event

router = APIRouter(tags=["Customers"])


@router.post(
    "",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new customer",
)
def create_customer(
    payload: CustomerCreate,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Creates a new customer entity:
    - Validates email uniqueness -> 400 Bad Request
    - Sets password hash (uses provided password or default secure fallback)
    - Persists Customer record
    - Writes AuditLog entry
    - Returns HTTP 201 Created
    """
    clean_email = payload.email.strip().lower()
    existing_customer = db.query(Customer).filter(Customer.email == clean_email).first()
    if existing_customer:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Customer with email '{clean_email}' already exists.",
        )

    # Secure default password fallback if none provided
    raw_password = payload.password if payload.password else "CustomerDefault2026!"
    hashed_pw = hash_password(raw_password)

    now = utc_now()
    customer = Customer(
        name=payload.name.strip(),
        email=clean_email,
        company_name=payload.company_name.strip() if payload.company_name else None,
        password_hash=hashed_pw,
        is_active=True,
        role="customer",
        created_at=now,
        updated_at=now,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    # Centralized Audit Log
    performer = current_user.email if current_user else "admin"
    log_system_event(
        db=db,
        entity_type="customer",
        entity_id=customer.id,
        action="CUSTOMER_CREATED",
        old_value=None,
        new_value={
            "name": customer.name,
            "email": customer.email,
            "company_name": customer.company_name,
        },
        performed_by=performer,
    )

    return customer


@router.get(
    "",
    response_model=List[CustomerResponse],
    summary="List customers with optional pagination and search",
)
def list_customers(
    skip: int = Query(0, ge=0, description="Offset records to skip"),
    limit: int = Query(100, ge=1, le=500, description="Maximum records to return"),
    search: Optional[str] = Query(None, description="Search by name, email, or company"),
    db: Session = Depends(get_db),
):
    """
    Retrieves all registered customers with optional search filtering and pagination.
    """
    query = db.query(Customer)
    if search:
        search_pattern = f"%{search.strip()}%"
        query = query.filter(
            Customer.name.ilike(search_pattern)
            | Customer.email.ilike(search_pattern)
            | Customer.company_name.ilike(search_pattern)
        )

    customers = query.order_by(Customer.id.asc()).offset(skip).limit(limit).all()
    return customers


@router.get(
    "/{id}",
    response_model=CustomerDetailResponse,
    summary="Get customer by ID with subscription history",
)
def get_customer(
    id: int,
    db: Session = Depends(get_db),
):
    """
    Fetches customer by ID along with their complete subscriptions list.
    """
    customer = db.query(Customer).filter(Customer.id == id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer with ID {id} not found.",
        )

    subs_data = []
    for sub in customer.subscriptions:
        resp = SubscriptionResponse.model_validate(sub)
        resp.plan_name = sub.plan.name if sub.plan else None
        resp.plan_price = sub.plan.price if sub.plan else None
        resp.allowed_transitions = get_allowed_transitions(sub.status)
        subs_data.append(resp)

    customer_dict = {
        "id": customer.id,
        "name": customer.name,
        "email": customer.email,
        "company_name": customer.company_name,
        "role": customer.role,
        "is_active": customer.is_active,
        "created_at": customer.created_at,
        "updated_at": customer.updated_at,
        "subscriptions": subs_data,
    }
    return customer_dict


@router.put(
    "/{id}",
    response_model=CustomerResponse,
    summary="Update customer details",
)
def update_customer(
    id: int,
    payload: CustomerUpdate,
    current_user: Optional[Customer] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """
    Updates customer profile details and records modifications in AuditLog.
    """
    customer = db.query(Customer).filter(Customer.id == id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer with ID {id} not found.",
        )

    # Check for email conflict
    if payload.email:
        clean_email = payload.email.strip().lower()
        if clean_email != customer.email.lower():
            conflict = (
                db.query(Customer)
                .filter(Customer.email == clean_email, Customer.id != id)
                .first()
            )
            if conflict:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Email '{clean_email}' is already in use by another customer.",
                )

    old_data = {
        "name": customer.name,
        "email": customer.email,
        "company_name": customer.company_name,
    }

    if payload.name is not None:
        customer.name = payload.name.strip()
    if payload.email is not None:
        customer.email = payload.email.strip().lower()
    if payload.company_name is not None:
        customer.company_name = payload.company_name.strip() if payload.company_name else None

    now = utc_now()
    customer.updated_at = now
    db.commit()
    db.refresh(customer)

    new_data = {
        "name": customer.name,
        "email": customer.email,
        "company_name": customer.company_name,
    }

    # Centralized Audit Log
    performer = current_user.email if current_user else "admin"
    log_system_event(
        db=db,
        entity_type="customer",
        entity_id=customer.id,
        action="CUSTOMER_UPDATED",
        old_value=old_data,
        new_value=new_data,
        performed_by=performer,
    )

    return customer
