"""
Billing Cycle Engine Service.
Handles automated recurring billing cycles, renewals, and invoice generation.
"""

from datetime import timedelta
from typing import Dict, Any, List
from sqlalchemy.orm import Session

from app.models import (
    Subscription,
    BillingCycle,
    Invoice,
    Plan,
    SubscriptionStatus,
    BillingCycleStatus,
    InvoiceStatus,
    utc_now,
)
from app.services.audit import log_system_event


class BillingService:
    @staticmethod
    def process_renewals(db: Session, performed_by: str = "billing_engine") -> Dict[str, Any]:
        """
        Processes recurring billing renewals for all active subscriptions:
        1. Queries active subscriptions whose latest cycle_end_date is past or due.
        2. Marks the previous billing cycle as 'completed'.
        3. Computes next cycle duration based on Plan interval (+30d monthly, +365d yearly).
        4. Inserts a new pending BillingCycle record.
        5. Generates a corresponding draft Invoice (subtotal, 18% standard tax, total).
        6. Updates subscription timestamps and logs an immutable audit event.
        """
        now = utc_now()
        active_subs: List[Subscription] = (
            db.query(Subscription)
            .filter(Subscription.status == SubscriptionStatus.ACTIVE.value)
            .all()
        )

        processed_subscriptions = 0
        new_invoices_created = 0
        details = []

        for sub in active_subs:
            # Locate latest billing cycle
            latest_cycle = (
                db.query(BillingCycle)
                .filter(BillingCycle.subscription_id == sub.id)
                .order_by(BillingCycle.cycle_end_date.desc())
                .first()
            )

            # Check if renewal is due (past cycle_end_date, or no cycle exists yet)
            is_due = False
            if latest_cycle is None:
                is_due = True
            elif latest_cycle.cycle_end_date <= now:
                is_due = True

            if not is_due:
                continue

            plan = sub.plan or db.query(Plan).filter(Plan.id == sub.plan_id).first()
            if not plan:
                continue

            # 1. Complete previous billing cycle
            old_cycle_id = None
            if latest_cycle:
                old_cycle_id = latest_cycle.id
                if latest_cycle.status == BillingCycleStatus.PENDING.value:
                    latest_cycle.status = BillingCycleStatus.COMPLETED.value
                    latest_cycle.updated_at = now

            # 2. Calculate new renewal window
            interval_days = 365 if plan.billing_interval == "yearly" else 30
            new_cycle_start = latest_cycle.cycle_end_date if latest_cycle else now
            new_cycle_end = new_cycle_start + timedelta(days=interval_days)
            new_renewal_date = new_cycle_end

            # 3. Create NEW BillingCycle record
            new_cycle = BillingCycle(
                subscription_id=sub.id,
                cycle_start_date=new_cycle_start,
                cycle_end_date=new_cycle_end,
                renewal_date=new_renewal_date,
                status=BillingCycleStatus.PENDING.value,
                created_at=now,
                updated_at=now,
            )
            db.add(new_cycle)
            db.flush()

            # 4. Generate new draft Invoice
            subtotal = round(float(plan.price), 2)
            tax_rate = 0.18  # 18% standard VAT/GST
            tax_amount = round(subtotal * tax_rate, 2)
            total_amount = round(subtotal + tax_amount, 2)
            invoice_num = f"INV-{sub.id}-{int(now.timestamp())}-{new_cycle.id}"

            new_invoice = Invoice(
                invoice_number=invoice_num,
                subscription_id=sub.id,
                customer_id=sub.customer_id,
                billing_cycle_id=new_cycle.id,
                invoice_date=now,
                due_date=now + timedelta(days=15),
                subtotal=subtotal,
                tax_amount=tax_amount,
                total_amount=total_amount,
                status=InvoiceStatus.DRAFT.value,
                created_at=now,
                updated_at=now,
            )
            db.add(new_invoice)

            # 5. Update Subscription
            sub.end_date = new_cycle_end
            sub.updated_at = now

            # 6. Record immutable Audit Log
            log_system_event(
                db=db,
                entity_type="subscription",
                entity_id=sub.id,
                action="BILLING_CYCLE_GENERATED",
                old_value={
                    "cycle_id": old_cycle_id,
                    "cycle_status": "completed" if latest_cycle else None,
                },
                new_value={
                    "new_cycle_id": new_cycle.id,
                    "cycle_start": new_cycle_start.isoformat(),
                    "cycle_end": new_cycle_end.isoformat(),
                    "invoice_number": invoice_num,
                    "total_amount": total_amount,
                },
                performed_by=performed_by,
            )

            processed_subscriptions += 1
            new_invoices_created += 1
            details.append({
                "subscription_id": sub.id,
                "customer_id": sub.customer_id,
                "invoice_number": invoice_num,
                "total_amount": total_amount,
                "cycle_end": new_cycle_end.isoformat(),
            })

        db.commit()

        return {
            "processed_subscriptions": processed_subscriptions,
            "new_invoices_created": new_invoices_created,
            "details": details,
        }
