"""
Pydantic Schemas for Request and Response Payloads.
Uses standard Pydantic types without external email-validator dependencies to adhere strictly
to Windows 11 Python 3.14 Application Control constraints.
"""

import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def validate_email_str(value: str) -> str:
    cleaned = value.strip().lower()
    if not EMAIL_REGEX.match(cleaned):
        raise ValueError("Invalid email format.")
    return cleaned


# ==========================================
# Authentication Schemas
# ==========================================

class SignupRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, example="Jane Doe")
    email: str = Field(..., min_length=5, max_length=255, example="jane.doe@example.com")
    password: str = Field(..., min_length=6, max_length=100, example="SuperSecret123!")
    company_name: Optional[str] = Field(None, max_length=150, example="Acme Innovations")

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        return validate_email_str(v)


class SigninRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255, example="jane.doe@example.com")
    password: str = Field(..., min_length=1, example="SuperSecret123!")

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        return validate_email_str(v)


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    company_name: Optional[str] = None
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ==========================================
# Plan Management Schemas
# ==========================================

class PlanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, example="Pro Growth Tier")
    price: float = Field(..., ge=0.0, example=49.99)
    billing_interval: str = Field("monthly", example="monthly")
    trial_days: int = Field(14, ge=0, example=14)
    features: Optional[Dict[str, Any]] = Field(None, example={"seats": 5, "support": "24/7 Priority"})

    @field_validator("billing_interval")
    @classmethod
    def check_interval(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if v_clean not in {"monthly", "yearly"}:
            raise ValueError("billing_interval must be 'monthly' or 'yearly'")
        return v_clean


class PlanUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    price: Optional[float] = Field(None, ge=0.0)
    billing_interval: Optional[str] = None
    trial_days: Optional[int] = Field(None, ge=0)
    features: Optional[Dict[str, Any]] = None

    @field_validator("billing_interval")
    @classmethod
    def check_interval(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_clean = v.strip().lower()
            if v_clean not in {"monthly", "yearly"}:
                raise ValueError("billing_interval must be 'monthly' or 'yearly'")
            return v_clean
        return v


class PlanResponse(BaseModel):
    id: int
    name: str
    price: float
    billing_interval: str
    trial_days: int
    features: Optional[Any] = None
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ==========================================
# Customer Management Schemas
# ==========================================

class CustomerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, example="Acme Corp")
    email: str = Field(..., max_length=255, example="billing@acme.com")
    company_name: Optional[str] = Field(None, max_length=150, example="Acme Enterprises LLC")
    password: Optional[str] = Field(None, min_length=6, max_length=100, example="CustomerPass123!")

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        return validate_email_str(v)


class CustomerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[str] = Field(None, max_length=255)
    company_name: Optional[str] = Field(None, max_length=150)

    @field_validator("email")
    @classmethod
    def check_email(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return validate_email_str(v)
        return v


class CustomerResponse(BaseModel):
    id: int
    name: str
    email: str
    company_name: Optional[str] = None
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ==========================================
# Subscription Schemas
# ==========================================

class CreateSubscriptionRequest(BaseModel):
    customer_id: Optional[int] = Field(None, description="Required for direct admin creation, defaults to current_user if omitted", example=1)
    plan_id: int = Field(..., example=1)
    start_immediately_active: bool = Field(False, description="Override trial period and activate immediately", example=False)
    status: Optional[str] = Field(None, example="trial")


class SubscriptionPlanChangeRequest(BaseModel):
    new_plan_id: int = Field(..., example=2)


class SubscriptionTransitionRequest(BaseModel):
    new_status: str = Field(
        ...,
        description="Target status: 'trial', 'active', 'paused', 'past_due', 'cancelled'",
        example="active",
    )


class SubscriptionResponse(BaseModel):
    id: int
    customer_id: int
    plan_id: int
    status: str
    start_date: datetime
    end_date: Optional[datetime] = None
    trial_end_date: Optional[datetime] = None
    paused_at: Optional[datetime] = None
    activated_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    status_changed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    plan_name: Optional[str] = None
    plan_price: Optional[float] = None
    allowed_transitions: Optional[List[str]] = None

    class Config:
        from_attributes = True


class CustomerDetailResponse(CustomerResponse):
    subscriptions: List[SubscriptionResponse] = []


class SubscriptionTransitionsResponse(BaseModel):
    subscription_id: int
    current_status: str
    allowed_transitions: List[str]
    is_terminal: bool


# ==========================================
# Billing Engine & Invoice Schemas
# ==========================================

class BillingEngineTriggerResponse(BaseModel):
    processed_subscriptions: int
    new_invoices_created: int
    details: Optional[List[Dict[str, Any]]] = None


class InvoiceResponse(BaseModel):
    id: int
    invoice_number: str
    subscription_id: int
    customer_id: int
    invoice_date: datetime
    due_date: datetime
    subtotal: float
    tax_amount: float
    total_amount: float
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BillingCycleResponse(BaseModel):
    id: int
    subscription_id: int
    cycle_start_date: datetime
    cycle_end_date: datetime
    renewal_date: datetime
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ==========================================
# Audit Logging Schemas
# ==========================================

class AuditLogResponse(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    action: str
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    performed_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
