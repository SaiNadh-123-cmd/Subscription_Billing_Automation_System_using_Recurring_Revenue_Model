"""
Authentication API Endpoints.
Provides Signup, Signin (supporting JSON and OAuth2 Form data), and Me profile endpoint.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Customer, Subscription, Plan, SubscriptionStatus, utc_now
from app.schemas import SignupRequest, SigninRequest, TokenResponse, UserResponse
from app.services.audit import log_system_event
from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new customer account",
)
def signup(
    payload: SignupRequest,
    db: Session = Depends(get_db),
):
    """
    Registers a new customer:
    - Validates email uniqueness
    - Securely hashes password via pbkdf2_sha256
    - Persists customer record
    - Writes an audit log entry
    - Automatically provisions a default Trial subscription (if plans exist)
    - Returns JWT access token and user profile
    """
    existing_user = db.query(Customer).filter(Customer.email == payload.email.lower()).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A customer with this email address already exists.",
        )

    hashed_pw = hash_password(payload.password)
    now = utc_now()

    customer = Customer(
        name=payload.name.strip(),
        email=payload.email.lower().strip(),
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

    # Automatically attach a default Trial subscription if a plan exists
    default_plan = db.query(Plan).first()
    if not default_plan:
        # Create a default plan if none exists yet
        default_plan = Plan(
            name="Starter Cloud Tier",
            price=29.00,
            billing_interval="monthly",
            trial_days=14,
            features={"seats": 3, "api_access": True, "support": "Standard"},
            created_at=now,
            updated_at=now,
        )
        db.add(default_plan)
        db.commit()
        db.refresh(default_plan)

    # Attach initial trial subscription
    trial_end = now + timedelta(days=default_plan.trial_days)
    sub = Subscription(
        customer_id=customer.id,
        plan_id=default_plan.id,
        status=SubscriptionStatus.TRIAL.value,
        start_date=now,
        end_date=trial_end,
        trial_end_date=trial_end,
        status_changed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(sub)

    # Audit log
    db.commit()
    log_system_event(
        db=db,
        entity_type="customer",
        entity_id=customer.id,
        action="CUSTOMER_CREATED",
        old_value=None,
        new_value={"name": customer.name, "email": customer.email, "role": customer.role},
        performed_by="signup_form",
    )

    token = create_access_token(
        data={"sub": customer.email, "id": customer.id, "role": customer.role}
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse.model_validate(customer),
    )


@router.post(
    "/signin",
    response_model=TokenResponse,
    summary="Sign in with email and password",
)
async def signin(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Authenticates a user and returns a signed JWT token.
    Supports both JSON payloads ({"email": "...", "password": "..."})
    and standard OAuth2 form data (username=...&password=...) for Swagger UI.
    """
    content_type = request.headers.get("content-type", "")
    email: Optional[str] = None
    password: Optional[str] = None

    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        email = form.get("username") or form.get("email")
        password = form.get("password")
    else:
        try:
            body = await request.json()
            email = body.get("email")
            password = body.get("password")
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body. Expected JSON or form data.",
            )

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email and password are required.",
        )

    customer = db.query(Customer).filter(Customer.email == email.lower().strip()).first()
    if not customer or not verify_password(password, customer.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not customer.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer account is inactive. Please contact support.",
        )

    # Audit log login event
    log_system_event(
        db=db,
        entity_type="customer",
        entity_id=customer.id,
        action="CUSTOMER_LOGIN",
        old_value=None,
        new_value={"ip": request.client.host if request.client else "unknown"},
        performed_by=customer.email,
    )

    token = create_access_token(
        data={"sub": customer.email, "id": customer.id, "role": customer.role}
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse.model_validate(customer),
    )


@router.get(
    "/me",
    summary="Get current authenticated user profile and subscriptions",
)
def get_me(
    current_user: Customer = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns profile information for the authenticated customer along with their
    active subscriptions, associated plan, and allowed next state transitions.
    """
    subscriptions_data = []
    from app.services.state_machine import get_allowed_transitions

    for sub in current_user.subscriptions:
        plan_name = sub.plan.name if sub.plan else "Custom Plan"
        plan_price = sub.plan.price if sub.plan else 0.0
        allowed = get_allowed_transitions(sub.status)

        subscriptions_data.append({
            "id": sub.id,
            "status": sub.status,
            "start_date": sub.start_date.isoformat() if sub.start_date else None,
            "end_date": sub.end_date.isoformat() if sub.end_date else None,
            "created_at": sub.created_at.isoformat() if sub.created_at else None,
            "plan_id": sub.plan_id,
            "plan_name": plan_name,
            "plan_price": plan_price,
            "allowed_transitions": allowed,
        })

    return {
        "user": {
            "id": current_user.id,
            "name": current_user.name,
            "email": current_user.email,
            "company_name": current_user.company_name,
            "role": current_user.role,
            "is_active": current_user.is_active,
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
        },
        "subscriptions": subscriptions_data,
    }
