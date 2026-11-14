import sys
import os

sys.path.insert(0, os.path.abspath("."))
import app

from sqlalchemy import inspect
from app.database import engine, Base, SessionLocal
from app import models
from app.auth import hash_password, verify_password, create_access_token, decode_access_token
from app.services.state_machine import (
    transition_subscription,
    can_transition,
    get_allowed_transitions,
    InvalidStateTransitionError,
    ALLOWED_TRANSITIONS,
)

def main():
    print("=" * 60)
    print("STEP 1: Re-creating Database Schema in PostgreSQL...")
    print("=" * 60)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("Tables dropped and recreated successfully.")

    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print("Existing tables:", tables)

    customer_cols = [c["name"] for c in inspector.get_columns("customers")]
    print("Customers columns:", customer_cols)
    assert "password_hash" in customer_cols
    assert "is_active" in customer_cols
    assert "role" in customer_cols
    print("✓ Customer schema verified with auth columns.")

    print("\n" + "=" * 60)
    print("STEP 2: Testing Password Hashing & JWT...")
    print("=" * 60)
    pw = "SuperSecurePassword2026!"
    hashed = hash_password(pw)
    print("Hashed password:", hashed)
    assert verify_password(pw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False
    print("✓ Password hashing & verification passed.")

    token = create_access_token({"sub": "test@example.com", "role": "customer"})
    decoded = decode_access_token(token)
    print("Decoded token subject:", decoded["sub"])
    assert decoded["sub"] == "test@example.com"
    print("✓ JWT creation & decoding passed.")

    print("\n" + "=" * 60)
    print("STEP 3: Testing Subscription Status State Machine Transitions...")
    print("=" * 60)
    db = SessionLocal()
    try:
        now = models.utc_now()
        # Seed Plan
        plan = models.Plan(
            name="Cloud Enterprise",
            price=199.0,
            billing_interval="monthly",
            trial_days=14,
        )
        db.add(plan)

        # Seed Customer
        customer = models.Customer(
            name="Alice Walker",
            email="alice@cloudcorp.com",
            company_name="CloudCorp",
            password_hash=hash_password("AlicePassword123!"),
            is_active=True,
            role="customer",
        )
        db.add(customer)
        db.commit()
        db.refresh(plan)
        db.refresh(customer)

        # Create Subscription in 'trial'
        sub = models.Subscription(
            customer_id=customer.id,
            plan_id=plan.id,
            status="trial",
            start_date=now,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        print(f"Initial subscription created: status='{sub.status}'")
        assert sub.status == "trial"

        # Check allowed transitions from trial: {"active", "cancelled"}
        allowed_from_trial = get_allowed_transitions(sub.status)
        print("Allowed transitions from 'trial':", allowed_from_trial)
        assert set(allowed_from_trial) == {"active", "cancelled"}

        # Attempt INVALID transition: trial -> past_due
        print("\nTesting INVALID transition: trial -> past_due (should fail)...")
        try:
            transition_subscription(db, sub.id, "past_due", performed_by="tester")
            assert False, "Should have raised InvalidStateTransitionError!"
        except Exception as e:
            print(f"✓ Correctly rejected: {e.detail}")

        # Execute VALID transition: trial -> active
        print("\nExecuting VALID transition: trial -> active...")
        sub = transition_subscription(db, sub.id, "active", performed_by="payment_webhook")
        print(f"Updated status: '{sub.status}'")
        assert sub.status == "active"

        # Check allowed transitions from active: {"past_due", "cancelled"}
        allowed_from_active = get_allowed_transitions(sub.status)
        print("Allowed transitions from 'active':", allowed_from_active)
        assert set(allowed_from_active) == {"past_due", "cancelled"}

        # Execute VALID transition: active -> past_due
        print("\nExecuting VALID transition: active -> past_due...")
        sub = transition_subscription(db, sub.id, "past_due", performed_by="dunning_service")
        print(f"Updated status: '{sub.status}'")
        assert sub.status == "past_due"

        # Execute VALID transition: past_due -> active (recovering after payment)
        print("\nExecuting VALID transition: past_due -> active (re-activation)...")
        sub = transition_subscription(db, sub.id, "active", performed_by="retry_charge_job")
        print(f"Updated status: '{sub.status}'")
        assert sub.status == "active"

        # Execute VALID transition: active -> cancelled (terminal)
        print("\nExecuting VALID transition: active -> cancelled (terminal)...")
        sub = transition_subscription(db, sub.id, "cancelled", performed_by="customer_portal")
        print(f"Updated status: '{sub.status}', end_date={sub.end_date}")
        assert sub.status == "cancelled"
        assert sub.end_date is not None

        # Check allowed transitions from cancelled: empty set
        allowed_from_cancelled = get_allowed_transitions(sub.status)
        print("Allowed transitions from 'cancelled':", allowed_from_cancelled)
        assert len(allowed_from_cancelled) == 0

        # Attempt INVALID transition out of terminal cancelled state
        print("\nTesting INVALID transition out of terminal cancelled: cancelled -> active...")
        try:
            transition_subscription(db, sub.id, "active", performed_by="tester")
            assert False, "Should have raised InvalidStateTransitionError!"
        except Exception as e:
            print(f"✓ Correctly rejected: {e.detail}")

        # Check AuditLogs
        logs = db.query(models.AuditLog).filter(models.AuditLog.entity_id == sub.id).all()
        print(f"\nAudit log entries recorded for subscription: {len(logs)}")
        for log in logs:
            print(f"  - [{log.created_at.strftime('%H:%M:%S')}] {log.action}: {log.old_value} -> {log.new_value} (by {log.performed_by})")
        assert len(logs) == 4

        print("\n✓ ALL STATE MACHINE TRANSITIONS AND AUDIT LOGS VERIFIED!")
    finally:
        db.close()

if __name__ == "__main__":
    main()
