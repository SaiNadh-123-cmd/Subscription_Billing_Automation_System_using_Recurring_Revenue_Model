"""
SQLAlchemy Core Billing Models for Subscription Billing Automation System.

Defines 7 core database models:
1. Plan (plans)
2. Customer (customers) - includes user authentication credentials
3. Subscription (subscriptions) - governed by strict state machine
4. BillingCycle (billing_cycles)
5. Invoice (invoices)
6. Payment (payments)
7. AuditLog (audit_logs)
"""

import enum
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


def utc_now() -> datetime:
    """Helper returning timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


# ==========================================
# Enums / Constants
# ==========================================

class BillingInterval(str, enum.Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


class SubscriptionStatus(str, enum.Enum):
    """
    Subscription lifecycle states governed by strict state machine.
    """
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class BillingCycleStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    PAID = "paid"
    UNPAID = "unpaid"
    VOID = "void"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    REFUNDED = "refunded"


class AuditEntityType(str, enum.Enum):
    CUSTOMER = "customer"
    PLAN = "plan"
    SUBSCRIPTION = "subscription"
    INVOICE = "invoice"
    PAYMENT = "payment"


# ==========================================
# 1. Plan Model
# ==========================================

class Plan(Base):
    """
    Subscription plan definition including pricing, interval, and trial period.
    """
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    price = Column(Float, nullable=False)
    billing_interval = Column(String(20), nullable=False, default=BillingInterval.MONTHLY.value)
    trial_days = Column(Integer, default=14, nullable=False)
    features = Column(JSON, nullable=True)
    is_archived = Column(Boolean, default=False, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    subscriptions = relationship("Subscription", back_populates="plan")

    def __repr__(self) -> str:
        return f"<Plan(id={self.id}, name='{self.name}', price={self.price}, interval='{self.billing_interval}')>"


# ==========================================
# 2. Customer Model (Auth + Profile)
# ==========================================

class Customer(Base):
    """
    Customer entity with authentication credentials and billing profile.
    """
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    company_name = Column(String(150), nullable=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    role = Column(String(30), default="customer", nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    subscriptions = relationship("Subscription", back_populates="customer", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="customer", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Customer(id={self.id}, name='{self.name}', email='{self.email}', role='{self.role}')>"


# ==========================================
# 3. Subscription Model (State Machine Linked)
# ==========================================

class Subscription(Base):
    """
    Recurring subscription linking a customer to a plan with status and date tracking.
    Valid statuses: 'trial', 'active', 'past_due', 'cancelled'
    """
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = Column(String(30), nullable=False, default=SubscriptionStatus.TRIAL.value, index=True)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=True)
    trial_end_date = Column(DateTime(timezone=True), nullable=True)
    paused_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    status_changed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    customer = relationship("Customer", back_populates="subscriptions")
    plan = relationship("Plan", back_populates="subscriptions")
    billing_cycles = relationship("BillingCycle", back_populates="subscription", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="subscription", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Subscription(id={self.id}, customer_id={self.customer_id}, plan_id={self.plan_id}, status='{self.status}')>"


# ==========================================
# 4. BillingCycle Model
# ==========================================

class BillingCycle(Base):
    """
    Discrete recurring cycle windows for a subscription tracking renewal schedules.
    """
    __tablename__ = "billing_cycles"

    id = Column(Integer, primary_key=True, index=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)
    cycle_start_date = Column(DateTime(timezone=True), nullable=False)
    cycle_end_date = Column(DateTime(timezone=True), nullable=False)
    renewal_date = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(20), nullable=False, default=BillingCycleStatus.PENDING.value, index=True)
    created_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    subscription = relationship("Subscription", back_populates="billing_cycles")

    def __repr__(self) -> str:
        return f"<BillingCycle(id={self.id}, subscription_id={self.subscription_id}, status='{self.status}')>"


# ==========================================
# 5. Invoice Model
# ==========================================

class Invoice(Base):
    """
    Invoice generated for a subscription and customer with financial line item totals.
    """
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(50), unique=True, index=True, nullable=False)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_date = Column(DateTime(timezone=True), nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=False)
    subtotal = Column(Float, nullable=False, default=0.0)
    tax_amount = Column(Float, nullable=False, default=0.0)
    total_amount = Column(Float, nullable=False, default=0.0)
    status = Column(String(20), nullable=False, default=InvoiceStatus.DRAFT.value, index=True)
    created_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    subscription = relationship("Subscription", back_populates="invoices")
    customer = relationship("Customer", back_populates="invoices")
    payments = relationship("Payment", back_populates="invoice", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Invoice(id={self.id}, number='{self.invoice_number}', total={self.total_amount}, status='{self.status}')>"


# ==========================================
# 6. Payment Model
# ==========================================

class Payment(Base):
    """
    Payment transaction record capturing gateway references, payment method, and status.
    """
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_reference = Column(String(100), unique=True, index=True, nullable=False)
    amount = Column(Float, nullable=False)
    payment_method = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default=PaymentStatus.PENDING.value, index=True)
    payment_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    invoice = relationship("Invoice", back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment(id={self.id}, ref='{self.payment_reference}', amount={self.amount}, status='{self.status}')>"


# ==========================================
# 7. AuditLog Model
# ==========================================

class AuditLog(Base):
    """
    Audit log tracking state transitions and entity modifications across billing models.
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String(50), nullable=False, index=True)
    entity_id = Column(Integer, nullable=False, index=True)
    action = Column(String(100), nullable=False, index=True)
    old_value = Column(JSON, nullable=True)
    new_value = Column(JSON, nullable=True)
    performed_by = Column(String(100), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, entity='{self.entity_type}#{self.entity_id}', action='{self.action}')>"
