import sys
import os

# Ensure project root is in PYTHONPATH and import app to initialize hooks
sys.path.insert(0, os.path.abspath("."))
import app

from sqlalchemy import inspect
from app.database import engine, Base
from app import models

def main():
    print("=" * 60)
    print("STEP 1: Dropping all existing tables in subscription_db...")
    print("=" * 60)
    Base.metadata.drop_all(bind=engine)
    print("Successfully dropped any pre-existing tables.")

    print("\n" + "=" * 60)
    print("STEP 2: Creating all 7 core billing tables with new schema...")
    print("=" * 60)
    Base.metadata.create_all(bind=engine)
    print("Successfully created all tables.")

    print("\n" + "=" * 60)
    print("STEP 3: Inspecting PostgreSQL Database Schema...")
    print("=" * 60)
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    print(f"Total tables detected: {len(table_names)}")
    print(f"Tables list: {table_names}\n")

    expected_tables = [
        "plans",
        "customers",
        "subscriptions",
        "billing_cycles",
        "invoices",
        "payments",
        "audit_logs",
    ]

    for table in expected_tables:
        print(f"--------------------------------------------------")
        print(f"TABLE: {table}")
        print(f"--------------------------------------------------")
        columns = inspector.get_columns(table)
        for col in columns:
            nullable_str = "NULL" if col["nullable"] else "NOT NULL"
            default_str = f" DEFAULT {col['default']}" if col.get("default") is not None else ""
            print(f"  - {col['name']:<20} : {str(col['type']):<25} {nullable_str:<10}{default_str}")

        pk = inspector.get_pk_constraint(table)
        print(f"  * Primary Key       : {pk.get('constrained_columns')}")

        fks = inspector.get_foreign_keys(table)
        if fks:
            for fk in fks:
                print(f"  * Foreign Key       : {fk['constrained_columns']} -> {fk['referred_table']}.{fk['referred_columns']} (ondelete={fk.get('options', {}).get('ondelete')})")

        indexes = inspector.get_indexes(table)
        if indexes:
            for idx in indexes:
                unique_str = " (UNIQUE)" if idx.get("unique") else ""
                print(f"  * Index             : {idx['name']} on {idx['column_names']}{unique_str}")
        print()

    print("=" * 60)
    print("STEP 4: Testing CRUD & Model Relationship Bindings...")
    print("=" * 60)
    from sqlalchemy.orm import Session
    from datetime import timedelta
    from app.database import SessionLocal

    db: Session = SessionLocal()
    try:
        now = models.utc_now()

        # 1. Plan
        plan = models.Plan(
            name="Pro Growth Plan",
            price=99.00,
            billing_interval="monthly",
            trial_days=14,
            features={"seats": 10, "api_access": True, "support": "24/7 priority"},
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        print(f"✓ Created Plan: {plan}")

        # 2. Customer
        customer = models.Customer(
            name="TechCorp Solutions",
            email="billing@techcorp.io",
            company_name="TechCorp International Inc.",
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)
        print(f"✓ Created Customer: {customer}")

        # 3. Subscription
        sub = models.Subscription(
            customer_id=customer.id,
            plan_id=plan.id,
            status="active",
            start_date=now,
            end_date=now + timedelta(days=365),
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        print(f"✓ Created Subscription: {sub}")

        # 4. BillingCycle
        cycle = models.BillingCycle(
            subscription_id=sub.id,
            cycle_start_date=now,
            cycle_end_date=now + timedelta(days=30),
            renewal_date=now + timedelta(days=30),
            status="pending",
        )
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
        print(f"✓ Created BillingCycle: {cycle}")

        # 5. Invoice
        invoice = models.Invoice(
            invoice_number="INV-2026-0001",
            subscription_id=sub.id,
            customer_id=customer.id,
            invoice_date=now,
            due_date=now + timedelta(days=15),
            subtotal=99.00,
            tax_amount=9.90,
            total_amount=108.90,
            status="unpaid",
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        print(f"✓ Created Invoice: {invoice}")

        # 6. Payment
        payment = models.Payment(
            invoice_id=invoice.id,
            payment_reference="PAY-REF-99887766",
            amount=108.90,
            payment_method="card",
            status="success",
            payment_date=now,
        )
        invoice.status = "paid"
        db.add(payment)
        db.commit()
        db.refresh(payment)
        print(f"✓ Created Payment: {payment}")

        # 7. AuditLog
        audit = models.AuditLog(
            entity_type="payment",
            entity_id=payment.id,
            action="CREATE",
            old_value=None,
            new_value={"status": "success", "amount": 108.90, "ref": "PAY-REF-99887766"},
            performed_by="system_webhook",
        )
        db.add(audit)
        db.commit()
        db.refresh(audit)
        print(f"✓ Created AuditLog: {audit}")

        # Verify relationship traversals
        print("\nChecking bidirectional relationship navigations:")
        assert sub.customer.email == "billing@techcorp.io"
        assert sub.plan.name == "Pro Growth Plan"
        assert len(sub.billing_cycles) == 1
        assert len(sub.invoices) == 1
        assert len(invoice.payments) == 1
        assert invoice.payments[0].payment_reference == "PAY-REF-99887766"
        assert invoice.customer.id == customer.id
        assert invoice.subscription.id == sub.id
        assert payment.invoice.id == invoice.id
        print("✓ All bidirectional relationships verified successfully!")

        # Clean up test rows
        db.delete(customer)
        db.delete(plan)
        db.delete(audit)
        db.commit()
        print("✓ Cleaned up test data.")

    finally:
        db.close()

    print("\n" + "=" * 60)
    print("ALL VERIFICATIONS COMPLETED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    main()
