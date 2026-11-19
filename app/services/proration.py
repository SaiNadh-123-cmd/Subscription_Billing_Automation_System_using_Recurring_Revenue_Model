"""
Proration & Plan Change Calculation Service.
Calculates fair mid-cycle upgrades and downgrades by computing:
- Unused current plan credit
- New plan charge for remaining cycle days
- Net amount due and proration adjustment invoices
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import (
    Subscription,
    Plan,
    BillingCycle,
    Invoice,
    InvoiceStatus,
    ProrationAdjustment,
    SubscriptionStatus,
    utc_now,
)
from app.services.audit import log_system_event

STANDARD_TAX_RATE = 0.18


class ProrationService:
    @staticmethod
    def calculate_proration(
        sub: Subscription,
        new_plan: Plan,
        now: Optional[datetime] = None,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """
        Pure calculation of unused credit, new plan charge, and net amount due.
        """
        if now is None:
            now = utc_now()

        current_plan = sub.plan
        if current_plan is None and db is not None:
            current_plan = db.query(Plan).filter(Plan.id == sub.plan_id).first()

        current_price = float(current_plan.price) if current_plan else 0.0
        new_price = float(new_plan.price)

        # 1. Determine cycle window
        latest_cycle = None
        if db is not None:
            latest_cycle = (
                db.query(BillingCycle)
                .filter(BillingCycle.subscription_id == sub.id)
                .order_by(BillingCycle.cycle_end_date.desc())
                .first()
            )

        if latest_cycle:
            cycle_start = latest_cycle.cycle_start_date
            cycle_end = latest_cycle.cycle_end_date
        elif sub.end_date and sub.start_date:
            cycle_start = sub.start_date
            cycle_end = sub.end_date
        else:
            cycle_start = sub.start_date or now
            interval_days = 365 if (current_plan and current_plan.billing_interval == "yearly") else 30
            cycle_end = cycle_start + timedelta(days=interval_days)

        # 2. Days calculation
        total_days = max(1, (cycle_end.date() - cycle_start.date()).days)

        if now.date() >= cycle_end.date():
            remaining_days = 0
        else:
            remaining_days = max(0, (cycle_end.date() - now.date()).days)

        remaining_days = min(remaining_days, total_days)

        # Edge case: same day modification within active cycle
        if remaining_days == 0 and now < cycle_end:
            remaining_days = 1

        # 3. Proration amounts
        if sub.status == SubscriptionStatus.TRIAL.value:
            # During trial, customer has not paid, so unused credit is 0
            unused_credit = 0.0
            new_plan_charge = round(new_price, 2)
            net_amount_due = new_plan_charge
            action_type = "upgrade"
        else:
            ratio = remaining_days / float(total_days)
            unused_credit = round(current_price * ratio, 2)
            new_plan_charge = round(new_price * ratio, 2)
            net_amount_due = round(new_plan_charge - unused_credit, 2)

            if net_amount_due > 0:
                action_type = "upgrade"
            elif net_amount_due < 0:
                action_type = "downgrade"
            else:
                action_type = "no_change"

        return {
            "subscription_id": sub.id,
            "current_plan_id": current_plan.id if current_plan else sub.plan_id,
            "current_plan_name": current_plan.name if current_plan else "Current Plan",
            "current_plan_price": current_price,
            "new_plan_id": new_plan.id,
            "new_plan_name": new_plan.name,
            "new_plan_price": new_price,
            "billing_interval": new_plan.billing_interval,
            "remaining_days": remaining_days,
            "total_cycle_days": total_days,
            "unused_credit": unused_credit,
            "new_plan_charge": new_plan_charge,
            "net_amount_due": net_amount_due,
            "action_type": action_type,
            "cycle_start_date": cycle_start,
            "cycle_end_date": cycle_end,
        }

    @staticmethod
    def execute_plan_change(
        db: Session,
        sub: Subscription,
        new_plan: Plan,
        performer: str = "system",
    ) -> Tuple[Subscription, ProrationAdjustment, Optional[Invoice]]:
        """
        Executes plan change, persists ProrationAdjustment, generates adjustment invoice,
        updates subscription, and logs audit record.
        """
        now = utc_now()
        calc = ProrationService.calculate_proration(sub=sub, new_plan=new_plan, now=now, db=db)

        old_plan_id = sub.plan_id
        old_plan_name = calc["current_plan_name"]
        new_plan_id = new_plan.id
        new_plan_name = new_plan.name

        net_amount = calc["net_amount_due"]
        created_invoice: Optional[Invoice] = None

        # Generate sequential invoice if amount is due or credit adjustment recorded
        date_str = now.strftime("%Y%m%d")
        existing_adj_count = (
            db.query(Invoice)
            .filter(Invoice.invoice_number.like(f"INV-ADJ-{date_str}-%"))
            .count()
        )
        inv_number = f"INV-ADJ-{date_str}-{(existing_adj_count + 1):04d}"

        if net_amount > 0:
            # Upgrade adjustment invoice
            subtotal = net_amount
            tax_amount = round(subtotal * STANDARD_TAX_RATE, 2)
            total_amount = round(subtotal + tax_amount, 2)

            created_invoice = Invoice(
                invoice_number=inv_number,
                subscription_id=sub.id,
                customer_id=sub.customer_id,
                invoice_date=now,
                due_date=now + timedelta(days=7),
                subtotal=subtotal,
                tax_amount=tax_amount,
                total_amount=total_amount,
                status=InvoiceStatus.UNPAID.value,
                created_at=now,
                updated_at=now,
            )
            db.add(created_invoice)
            db.flush()
        elif net_amount < 0:
            # Downgrade credit memo invoice (paid/settled immediately)
            cr_inv_number = f"INV-CR-{date_str}-{(existing_adj_count + 1):04d}"
            created_invoice = Invoice(
                invoice_number=cr_inv_number,
                subscription_id=sub.id,
                customer_id=sub.customer_id,
                invoice_date=now,
                due_date=now,
                subtotal=0.0,
                tax_amount=0.0,
                total_amount=0.0,
                status=InvoiceStatus.PAID.value,
                created_at=now,
                updated_at=now,
            )
            db.add(created_invoice)
            db.flush()

        # Create ProrationAdjustment entry
        adjustment = ProrationAdjustment(
            subscription_id=sub.id,
            customer_id=sub.customer_id,
            old_plan_id=old_plan_id,
            new_plan_id=new_plan_id,
            old_plan_name=old_plan_name,
            new_plan_name=new_plan_name,
            remaining_days=calc["remaining_days"],
            total_days=calc["total_cycle_days"],
            unused_credit=calc["unused_credit"],
            new_plan_charge=calc["new_plan_charge"],
            net_amount_due=net_amount,
            invoice_id=created_invoice.id if created_invoice else None,
            created_at=now,
        )
        db.add(adjustment)

        # Update Subscription
        sub.plan_id = new_plan.id
        sub.updated_at = now

        # If active, adjust end date to align with the new plan interval if changed
        if sub.status == SubscriptionStatus.ACTIVE.value:
            duration = 365 if new_plan.billing_interval == "yearly" else 30
            # Keep remaining cycle or extend to new interval duration
            if sub.end_date is None or sub.end_date <= now:
                sub.end_date = now + timedelta(days=duration)

        db.commit()
        db.refresh(sub)
        db.refresh(adjustment)
        if created_invoice:
            db.refresh(created_invoice)

        # Immutable Audit Log
        log_system_event(
            db=db,
            entity_type="subscription",
            entity_id=sub.id,
            action="PLAN_CHANGED",
            old_value={
                "plan_id": old_plan_id,
                "plan_name": old_plan_name,
            },
            new_value={
                "plan_id": new_plan_id,
                "plan_name": new_plan_name,
                "remaining_days": calc["remaining_days"],
                "unused_credit": calc["unused_credit"],
                "new_plan_charge": calc["new_plan_charge"],
                "net_amount_due": net_amount,
                "invoice_id": created_invoice.id if created_invoice else None,
                "invoice_number": created_invoice.invoice_number if created_invoice else None,
            },
            performed_by=performer,
        )

        return sub, adjustment, created_invoice
