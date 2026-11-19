"""
Unit verification for Proration & Plan Change Logic.
Tests:
- 15 days elapsed in 30-day month -> 50% credit, 50% charge -> ₹500 net amount due
- GET /subscriptions/{id}/proration-preview
- POST /subscriptions/{id}/change-plan
- GET /subscriptions/{id}/plan-history
- Verification of adjustment invoice creation with 18% tax
"""

import uuid
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import Plan, Customer, Subscription, BillingCycle, Invoice, ProrationAdjustment, AuditLog

client = TestClient(app)

def test_proration():
    run_id = uuid.uuid4().hex[:6]
    print(f"Testing Proration with run_id: {run_id}")

    # 1. Create Pro Plan (₹1000/month) and Premium Plan (₹2000/month)
    res_p1 = client.post("/plans", json={
        "name": f"Pro Plan {run_id}",
        "price": 1000.0,
        "billing_interval": "monthly",
        "trial_days": 0,
    })
    assert res_p1.status_code == 201, res_p1.text
    pro_plan_id = res_p1.json()["id"]

    res_p2 = client.post("/plans", json={
        "name": f"Premium Plan {run_id}",
        "price": 2000.0,
        "billing_interval": "monthly",
        "trial_days": 0,
    })
    assert res_p2.status_code == 201, res_p2.text
    premium_plan_id = res_p2.json()["id"]

    # 2. Create Customer
    res_c = client.post("/customers", json={
        "name": f"Rohan Sharma {run_id}",
        "email": f"rohan.sharma_{run_id}@example.in",
        "company_name": "Sharma Tech",
    })
    assert res_c.status_code == 201, res_c.text
    customer_id = res_c.json()["id"]

    # 3. Create Subscription on Pro Plan (immediately active)
    res_s = client.post("/subscriptions", json={
        "customer_id": customer_id,
        "plan_id": pro_plan_id,
        "start_immediately_active": True,
    })
    assert res_s.status_code == 201, res_s.text
    sub_id = res_s.json()["id"]

    # 4. Set cycle so exactly 15 days remain out of 30 days
    # Subscribed on: 1 July, Now: 15 July, Remaining: 15 days out of 30
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        cycle = db.query(BillingCycle).filter(BillingCycle.subscription_id == sub_id).first()
        assert cycle is not None
        cycle.cycle_start_date = now - timedelta(days=15)
        cycle.cycle_end_date = now + timedelta(days=15)
        cycle.renewal_date = cycle.cycle_end_date
        db.commit()
    finally:
        db.close()

    # 5. Test GET /subscriptions/{id}/proration-preview?new_plan_id=...
    prev_res = client.get(f"/subscriptions/{sub_id}/proration-preview?new_plan_id={premium_plan_id}")
    assert prev_res.status_code == 200, prev_res.text
    prev_data = prev_res.json()
    print("Preview Data:", prev_data)
    assert prev_data["remaining_days"] == 15
    assert prev_data["total_cycle_days"] == 30
    assert prev_data["unused_credit"] == 500.0, f"Expected 500.0, got {prev_data['unused_credit']}"
    assert prev_data["new_plan_charge"] == 1000.0, f"Expected 1000.0, got {prev_data['new_plan_charge']}"
    assert prev_data["net_amount_due"] == 500.0, f"Expected 500.0, got {prev_data['net_amount_due']}"
    assert prev_data["action_type"] == "upgrade"
    print("✓ Proration preview calculation verified (Credit ₹500, Charge ₹1000, Amount Due ₹500)")

    # 6. Test POST /subscriptions/{id}/change-plan
    change_res = client.post(f"/subscriptions/{sub_id}/change-plan", json={"new_plan_id": premium_plan_id})
    assert change_res.status_code == 200, change_res.text
    change_data = change_res.json()
    assert change_data["plan_id"] == premium_plan_id
    assert change_data["proration"]["unused_credit"] == 500.0
    assert change_data["proration"]["new_plan_charge"] == 1000.0
    assert change_data["proration"]["net_amount_due"] == 500.0
    assert change_data["invoice_number"] is not None
    print("✓ Plan change executed with Proration adjustment invoice:", change_data["invoice_number"])

    # 7. Check adjustment Invoice
    db = SessionLocal()
    try:
        inv = db.query(Invoice).filter(Invoice.id == change_data["invoice_id"]).first()
        assert inv is not None
        assert inv.subtotal == 500.0
        assert inv.tax_amount == round(500.0 * 0.18, 2)  # 90.0
        assert inv.total_amount == 590.0
        assert inv.status == "unpaid"
        print("✓ Adjustment invoice verified in database (Subtotal: ₹500, Tax: ₹90, Total: ₹590)")

        # 8. Check ProrationAdjustment record
        adj = db.query(ProrationAdjustment).filter(ProrationAdjustment.subscription_id == sub_id).first()
        assert adj is not None
        assert adj.old_plan_name == f"Pro Plan {run_id}"
        assert adj.new_plan_name == f"Premium Plan {run_id}"
        assert adj.net_amount_due == 500.0
        print("✓ ProrationAdjustment record verified")
    finally:
        db.close()

    # 9. Test GET /subscriptions/{id}/plan-history
    hist_res = client.get(f"/subscriptions/{sub_id}/plan-history")
    assert hist_res.status_code == 200, hist_res.text
    hist_data = hist_res.json()
    assert len(hist_data) >= 1
    assert hist_data[0]["old_plan_name"] == f"Pro Plan {run_id}"
    assert hist_data[0]["new_plan_name"] == f"Premium Plan {run_id}"
    assert hist_data[0]["net_amount_due"] == 500.0
    print("✓ Plan change history endpoint verified:", hist_data[0])

    print("ALL PRORATION UNIT TESTS PASSED!")

if __name__ == "__main__":
    test_proration()
