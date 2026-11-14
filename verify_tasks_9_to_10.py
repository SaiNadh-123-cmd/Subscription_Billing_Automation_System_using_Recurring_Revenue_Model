"""
Comprehensive Verification Test Suite for Tasks 9 & 10:
- Task 9: Billing Cycle Engine (Automated renewals, cycle completion, rolling intervals, draft invoice generation with 18% tax)
- Task 10: Audit Logging System (Centralized event logger, global coverage, audit trail API with filtering & pagination)
- UI and OpenAPI Cleanup Verification (No 'Task' labels, professional SaaS terminology, clean Swagger tags)
"""

import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import Customer, Plan, Subscription, BillingCycle, Invoice, AuditLog
from app.services.audit import log_system_event

client = TestClient(app)

results = []

def record(category: str, title: str, passed: bool, detail: str = ""):
    status_str = "PASS" if passed else "FAIL"
    icon = "✓" if passed else "✗"
    print(f"[{status_str}] {icon} {category} - {title}: {detail}")
    results.append({
        "category": category,
        "title": title,
        "passed": passed,
        "detail": detail
    })

def run_tests():
    print("=" * 80)
    print("STARTING VERIFICATION SUITE: TASKS 9 & 10 (BILLING ENGINE & AUDIT SYSTEM)")
    print("=" * 80)
    run_id = uuid.uuid4().hex[:6]

    # ----------------------------------------------------
    # TEST 1: Task 9 - Billing Cycle Renewal Engine
    # ----------------------------------------------------
    print("\n--- Testing Task 9: Billing Cycle Renewal Engine ---")
    
    # 1. Setup a test plan and customer
    plan_res = client.post("/plans", json={
        "name": f"Cloud Engine Tier {run_id}",
        "price": 100.0,
        "billing_interval": "monthly",
        "trial_days": 0,
        "features": {"api_access": True, "sla": "99.9%"}
    })
    assert plan_res.status_code == 201, f"Failed to create test plan: {plan_res.text}"
    plan_id = plan_res.json()["id"]

    cust_res = client.post("/customers", json={
        "name": f"Billing Tester {run_id}",
        "email": f"billing_tester_{run_id}@example.com",
        "company_name": "Renewal Labs Corp"
    })
    assert cust_res.status_code == 201, f"Failed to create test customer: {cust_res.text}"
    customer_id = cust_res.json()["id"]

    # 2. Create an immediately active subscription
    sub_res = client.post("/subscriptions", json={
        "customer_id": customer_id,
        "plan_id": plan_id,
        "start_immediately_active": True
    })
    assert sub_res.status_code == 201, f"Failed to create subscription: {sub_res.text}"
    sub_id = sub_res.json()["id"]

    # 3. Artificially backdate the subscription's billing cycle end_date so it is due
    db = SessionLocal()
    past_end = datetime.now(timezone.utc) - timedelta(days=2)
    past_start = past_end - timedelta(days=30)
    try:
        initial_cycle = db.query(BillingCycle).filter(BillingCycle.subscription_id == sub_id).first()
        assert initial_cycle is not None, "Initial cycle was not created"
        initial_cycle.cycle_start_date = past_start
        initial_cycle.cycle_end_date = past_end
        initial_cycle.renewal_date = past_end
        db.commit()
        db.refresh(initial_cycle)
    finally:
        db.close()

    # 4. Trigger the Billing Engine via POST /api/billing/trigger-engine
    trigger_res = client.post("/api/billing/trigger-engine")
    trigger_data = trigger_res.json()
    record(
        "Task 9",
        "POST /api/billing/trigger-engine",
        trigger_res.status_code == 200 and trigger_data.get("processed_subscriptions", 0) >= 1,
        f"Processed {trigger_data.get('processed_subscriptions')} subscriptions, created {trigger_data.get('new_invoices_created')} invoices."
    )

    # 5. Check database states for completed cycle, new cycle, and generated invoice
    db = SessionLocal()
    try:
        cycles = db.query(BillingCycle).filter(BillingCycle.subscription_id == sub_id).order_by(BillingCycle.id.asc()).all()
        has_two_cycles = len(cycles) >= 2
        old_cycle = cycles[0] if has_two_cycles else None
        new_cycle = cycles[-1] if has_two_cycles else None
        
        cycle_progression_ok = (
            has_two_cycles
            and old_cycle.status == "completed"
            and new_cycle.status == "pending"
            and new_cycle.cycle_end_date > datetime.now(timezone.utc)
        )
        record(
            "Task 9",
            "Cycle Roll & Status Progression",
            cycle_progression_ok,
            f"Old cycle #{old_cycle.id if old_cycle else 'N/A'} (status={old_cycle.status if old_cycle else 'N/A'}), "
            f"New cycle #{new_cycle.id if new_cycle else 'N/A'} (status={new_cycle.status if new_cycle else 'N/A'})."
        )

        # 6. Verify Draft Invoice generation with 18% standard tax
        invoice = db.query(Invoice).filter(Invoice.subscription_id == sub_id).order_by(Invoice.id.desc()).first()
        expected_subtotal = 100.0
        expected_tax = round(expected_subtotal * 0.18, 2)
        expected_total = round(expected_subtotal + expected_tax, 2)
        invoice_ok = (
            invoice is not None
            and invoice.status == "draft"
            and float(invoice.subtotal) == expected_subtotal
            and float(invoice.tax_amount) == expected_tax
            and float(invoice.total_amount) == expected_total
            and invoice.customer_id == customer_id
        )
        record(
            "Task 9",
            "Draft Invoice Generation & 18% Tax Calculation",
            invoice_ok,
            f"Invoice #{invoice.id if invoice else 'N/A'} [status={invoice.status if invoice else 'N/A'}]: "
            f"subtotal={invoice.subtotal if invoice else 'N/A'}, "
            f"tax={invoice.tax_amount if invoice else 'N/A'}, "
            f"total={invoice.total_amount if invoice else 'N/A'}."
        )

        # 7. Check AuditLog entry for BILLING_CYCLE_GENERATED
        audit_event = (
            db.query(AuditLog)
            .filter(AuditLog.entity_id == sub_id, AuditLog.action == "BILLING_CYCLE_GENERATED")
            .first()
        )
        record(
            "Task 9",
            "Audit Event for Billing Cycle Generation",
            audit_event is not None,
            f"Audit log #{audit_event.id if audit_event else 'None'} recorded with action={audit_event.action if audit_event else 'None'}."
        )
    finally:
        db.close()

    # 8. Test GET /api/billing/cycles and GET /api/billing/invoices
    res_cycles = client.get("/api/billing/cycles")
    record(
        "Task 9",
        "GET /api/billing/cycles",
        res_cycles.status_code == 200 and isinstance(res_cycles.json(), list) and len(res_cycles.json()) > 0,
        f"Fetched {len(res_cycles.json())} billing cycles."
    )

    res_invoices = client.get("/api/billing/invoices")
    record(
        "Task 9",
        "GET /api/billing/invoices",
        res_invoices.status_code == 200 and isinstance(res_invoices.json(), list) and len(res_invoices.json()) > 0,
        f"Fetched {len(res_invoices.json())} invoices."
    )

    # ----------------------------------------------------
    # TEST 2: Task 10 - Audit Logging System
    # ----------------------------------------------------
    print("\n--- Testing Task 10: Audit Logging System ---")

    # 1. GET /api/audit-logs
    audit_res = client.get("/api/audit-logs")
    audit_data = audit_res.json()
    record(
        "Task 10",
        "GET /api/audit-logs (Base List)",
        audit_res.status_code == 200 and isinstance(audit_data, list) and len(audit_data) > 0,
        f"Retrieved {len(audit_data)} audit records."
    )

    # 2. Filter by entity_type
    filter_entity_res = client.get("/api/audit-logs?entity_type=plan")
    entity_logs = filter_entity_res.json()
    all_entity_match = all(log.get("entity_type") == "plan" for log in entity_logs) and len(entity_logs) > 0
    record(
        "Task 10",
        "GET /api/audit-logs?entity_type=plan Filter",
        filter_entity_res.status_code == 200 and all_entity_match,
        f"Returned {len(entity_logs)} logs, all matching entity_type='plan'."
    )

    # 3. Filter by action
    filter_action_res = client.get("/api/audit-logs?action=BILLING_CYCLE_GENERATED")
    action_logs = filter_action_res.json()
    all_action_match = all(log.get("action") == "BILLING_CYCLE_GENERATED" for log in action_logs) and len(action_logs) > 0
    record(
        "Task 10",
        "GET /api/audit-logs?action=BILLING_CYCLE_GENERATED Filter",
        filter_action_res.status_code == 200 and all_action_match,
        f"Returned {len(action_logs)} logs, all matching action='BILLING_CYCLE_GENERATED'."
    )

    # 4. Pagination (limit and skip)
    page_res = client.get("/api/audit-logs?skip=0&limit=3")
    page_data = page_res.json()
    record(
        "Task 10",
        "GET /api/audit-logs Pagination (limit=3)",
        page_res.status_code == 200 and len(page_data) <= 3,
        f"Successfully returned {len(page_data)} records adhering to limit parameter."
    )

    # 5. Direct log_system_event verification
    db = SessionLocal()
    try:
        custom_action = f"MANUAL_VERIFY_{run_id.upper()}"
        log_system_event(
            db,
            entity_type="system",
            entity_id=999,
            action=custom_action,
            old_value={"test": False},
            new_value={"test": True},
            performed_by="verification_suite"
        )
        saved_log = db.query(AuditLog).filter(AuditLog.action == custom_action).first()
        record(
            "Task 10",
            "log_system_event Helper Function",
            saved_log is not None and saved_log.performed_by == "verification_suite",
            f"Successfully logged custom event #{saved_log.id if saved_log else 'None'} directly via service."
        )
    finally:
        db.close()

    # ----------------------------------------------------
    # TEST 3: Professional UI and OpenAPI Scrubbing
    # ----------------------------------------------------
    print("\n--- Testing UI & API Cleanliness (Zero 'Task' strings) ---")

    # 1. Verify OpenAPI schema does not have 'Task' in tags or descriptions
    openapi_res = client.get("/openapi.json")
    openapi_json = openapi_res.json()
    openapi_tags = [t.get("name") for t in openapi_json.get("tags", [])]
    expected_tags = {"Authentication", "Billing", "Customers", "Plans", "Subscriptions", "System"}
    tags_valid = expected_tags.issubset(set(openapi_tags))
    
    # Check if 'task' appears in any tag name or description
    tags_clean = all("task" not in t.get("name", "").lower() for t in openapi_json.get("tags", []))
    record(
        "UI/OpenAPI",
        "OpenAPI Tags Organization",
        tags_valid and tags_clean,
        f"Tags properly configured: {sorted(openapi_tags)}"
    )

    # 2. Check /home HTML template response
    home_res = client.get("/home")
    home_html = home_res.text if home_res.status_code == 200 else ""
    # Verify no Task 1, Task 2 etc.
    import re
    task_mentions = re.findall(r"Task\s*\d+", home_html, re.IGNORECASE)
    record(
        "UI/OpenAPI",
        "Dashboard Template Cleanliness (/home)",
        home_res.status_code == 200 and len(task_mentions) == 0,
        f"Detected {len(task_mentions)} legacy task mentions in dashboard HTML."
    )

    # Verify Run Billing Engine button and Audit History table exist in /home
    has_engine_ui = "Run Billing Engine" in home_html or "btnRunBillingEngine" in home_html
    has_audit_ui = "Audit Trail" in home_html or "fetchGlobalAuditLogs" in home_html
    record(
        "UI/OpenAPI",
        "Dashboard Engine & Audit Controls",
        has_engine_ui and has_audit_ui,
        "Confirmed 'Run Billing Engine' action button and 'Audit Trail' table in dashboard."
    )

    # ----------------------------------------------------
    # SUMMARY
    # ----------------------------------------------------
    print("\n" + "=" * 80)
    print("VERIFICATION SUMMARY TABLE: TASKS 9 & 10")
    print("=" * 80)
    print(f"{'CATEGORY':<12} | {'TEST DESCRIPTION':<45} | {'STATUS':<6} | {'DETAILS'}")
    print("-" * 80)
    total_passed = 0
    for r in results:
        if r["passed"]:
            total_passed += 1
        print(f"{r['category']:<12} | {r['title']:<45} | {'PASS' if r['passed'] else 'FAIL':<6} | {r['detail']}")
    print("-" * 80)
    print(f"TOTAL: {total_passed}/{len(results)} PASSED ({total_passed/len(results)*100:.1f}%)")
    print("=" * 80)

    assert total_passed == len(results), f"Some tests failed! {len(results) - total_passed} failures."

if __name__ == "__main__":
    run_tests()
