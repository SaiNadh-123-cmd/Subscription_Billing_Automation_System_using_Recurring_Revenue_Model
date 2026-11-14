"""
Verification Test Suite for Tasks 5, 6, 7, and 8.
Covers:
- Task 5: Plan Management (/plans)
- Task 6: Customer Management (/customers)
- Task 7: Subscription Creation (/subscriptions)
- Task 8: Subscription Lifecycle Operations (/subscriptions/{id}/...)
"""

import sys
import os
import uuid

sys.path.insert(0, os.path.abspath("."))
import app  # noqa: F401 - Initialize environment

from fastapi.testclient import TestClient
from app.main import app as fastapi_app
from app.database import SessionLocal
from app.models import BillingCycle, AuditLog

client = TestClient(fastapi_app)

test_results = []


def record_result(task: str, test_name: str, passed: bool, notes: str = ""):
    status_str = "PASS" if passed else "FAIL"
    test_results.append({
        "task": task,
        "test": test_name,
        "status": status_str,
        "notes": notes,
    })
    symbol = "✓" if passed else "✗"
    print(f"[{status_str}] {symbol} {task} - {test_name}: {notes}")


def run_full_verification():
    uid = uuid.uuid4().hex[:6]
    print("=" * 80)
    print(f"STARTING COMPREHENSIVE VERIFICATION: TASKS 5, 6, 7 & 8 (Run ID: {uid})")
    print("=" * 80)

    # ----------------------------------------------------
    # TASK 5: PLAN MANAGEMENT (/plans)
    # ----------------------------------------------------
    print("\n--- Testing Task 5: Plan Management APIs ---")
    plan_name = f"Enterprise Cloud Tier {uid}"
    plan_payload = {
        "name": plan_name,
        "price": 149.99,
        "billing_interval": "monthly",
        "trial_days": 14,
        "features": {"api_rate_limit": 10000, "seats": 25, "sla": "99.99%"}
    }

    # 1. POST /plans (Create Plan)
    res = client.post("/plans", json=plan_payload)
    if res.status_code == 201:
        created_plan = res.json()
        plan_id = created_plan["id"]
        record_result("Task 5", "POST /plans (Create Plan)", True, f"Created Plan #{plan_id} '{plan_name}'")
    else:
        record_result("Task 5", "POST /plans (Create Plan)", False, f"HTTP {res.status_code}: {res.text}")
        return

    # 2. Duplicate Plan Name Rejection
    res = client.post("/plans", json=plan_payload)
    record_result(
        "Task 5",
        "POST /plans (Duplicate Name Check)",
        res.status_code == 400,
        f"Correctly returned HTTP 400: {res.json().get('detail')}"
    )

    # 3. GET /plans (List Active Plans)
    res = client.get("/plans")
    plans_list = res.json()
    record_result(
        "Task 5",
        "GET /plans (List Active Plans)",
        res.status_code == 200 and any(p["id"] == plan_id for p in plans_list),
        f"Found {len(plans_list)} plans in catalog."
    )

    # 4. GET /plans/{id} (Get Plan by ID)
    res = client.get(f"/plans/{plan_id}")
    record_result(
        "Task 5",
        "GET /plans/{id} (Get Plan Details)",
        res.status_code == 200 and res.json()["name"] == plan_name,
        f"Retrieved plan #{plan_id} with price ${res.json().get('price')}."
    )

    # 5. PUT /plans/{id} (Update Plan)
    update_payload = {
        "price": 179.99,
        "trial_days": 21,
        "features": {"api_rate_limit": 20000, "seats": 50, "sla": "99.999%"}
    }
    res = client.put(f"/plans/{plan_id}", json=update_payload)
    record_result(
        "Task 5",
        "PUT /plans/{id} (Update Plan)",
        res.status_code == 200 and res.json()["price"] == 179.99 and res.json()["trial_days"] == 21,
        f"Price updated to $179.99, trial extended to 21 days."
    )

    # 6. Secondary plan for change-plan tests later
    secondary_plan_payload = {
        "name": f"Growth Tier {uid}",
        "price": 49.00,
        "billing_interval": "yearly",
        "trial_days": 0,
        "features": {"seats": 5}
    }
    res = client.post("/plans", json=secondary_plan_payload)
    secondary_plan_id = res.json()["id"]

    # 7. Ephemeral plan for delete test
    temp_plan_payload = {
        "name": f"Temporary Plan {uid}",
        "price": 9.99,
        "billing_interval": "monthly",
        "trial_days": 7
    }
    res = client.post("/plans", json=temp_plan_payload)
    temp_plan_id = res.json()["id"]
    res_del = client.delete(f"/plans/{temp_plan_id}")
    record_result(
        "Task 5",
        "DELETE /plans/{id} (Delete Unused Plan)",
        res_del.status_code == 200,
        f"Deleted unused temporary plan #{temp_plan_id}."
    )

    # 8. Plan Archiving: create another plan and archive it
    arch_plan_payload = {
        "name": f"Legacy Tier {uid}",
        "price": 19.99,
        "billing_interval": "monthly",
        "trial_days": 14
    }
    res = client.post("/plans", json=arch_plan_payload)
    arch_plan_id = res.json()["id"]
    res_arch = client.patch(f"/plans/{arch_plan_id}/archive")
    record_result(
        "Task 5",
        "PATCH /plans/{id}/archive (Archive Plan)",
        res_arch.status_code == 200 and res_arch.json()["is_archived"] is True,
        f"Plan #{arch_plan_id} successfully marked as is_archived=True."
    )

    # 9. Default GET /plans excludes archived
    res_no_arch = client.get("/plans")
    arch_excluded = not any(p["id"] == arch_plan_id for p in res_no_arch.json())
    res_with_arch = client.get("/plans?include_archived=true")
    arch_included = any(p["id"] == arch_plan_id for p in res_with_arch.json())
    record_result(
        "Task 5",
        "GET /plans?include_archived filter",
        arch_excluded and arch_included,
        "Archived plan omitted by default and included when include_archived=true."
    )

    # ----------------------------------------------------
    # TASK 6: CUSTOMER MANAGEMENT (/customers)
    # ----------------------------------------------------
    print("\n--- Testing Task 6: Customer Management APIs ---")
    customer_email = f"alex.mercer_{uid}@blackwatch.org"
    customer_payload = {
        "name": "Alex Mercer",
        "email": customer_email,
        "company_name": "Gentek Biochemical",
        "password": "SecurePassword2026!"
    }

    # 1. POST /customers (Create Customer)
    res = client.post("/customers", json=customer_payload)
    if res.status_code == 201:
        created_customer = res.json()
        customer_id = created_customer["id"]
        record_result("Task 6", "POST /customers (Create Customer)", True, f"Created Customer #{customer_id} ({customer_email}).")
    else:
        record_result("Task 6", "POST /customers (Create Customer)", False, f"HTTP {res.status_code}: {res.text}")
        return

    # 2. Duplicate Email Rejection
    res = client.post("/customers", json=customer_payload)
    record_result(
        "Task 6",
        "POST /customers (Duplicate Email Check)",
        res.status_code == 400,
        f"Correctly rejected duplicate with HTTP 400: {res.json().get('detail')}"
    )

    # 3. GET /customers (List Customers)
    res = client.get("/customers?limit=50")
    cust_list = res.json()
    record_result(
        "Task 6",
        "GET /customers (List Customers)",
        res.status_code == 200 and any(c["id"] == customer_id for c in cust_list),
        f"Listed {len(cust_list)} customers from PostgreSQL."
    )

    # 4. PUT /customers/{id} (Update Customer)
    update_cust_payload = {
        "company_name": "Gentek Bio-Defense Systems",
        "name": "Alexander J. Mercer"
    }
    res = client.put(f"/customers/{customer_id}", json=update_cust_payload)
    record_result(
        "Task 6",
        "PUT /customers/{id} (Update Customer)",
        res.status_code == 200 and res.json()["company_name"] == "Gentek Bio-Defense Systems",
        f"Customer #{customer_id} company updated."
    )

    # 5. GET /customers/{id} (Get Customer Details with Subscriptions)
    res = client.get(f"/customers/{customer_id}")
    record_result(
        "Task 6",
        "GET /customers/{id} (Get Customer Details)",
        res.status_code == 200 and "subscriptions" in res.json(),
        f"Customer retrieved with {len(res.json()['subscriptions'])} initial subscriptions."
    )

    # ----------------------------------------------------
    # TASK 7: SUBSCRIPTION CREATION (/subscriptions)
    # ----------------------------------------------------
    print("\n--- Testing Task 7: Subscription Creation API ---")

    # 1. Non-existent customer check
    res = client.post("/subscriptions", json={"customer_id": 999999, "plan_id": plan_id})
    record_result(
        "Task 7",
        "POST /subscriptions (Non-existent Customer 404)",
        res.status_code == 404,
        f"Correctly returned 404: {res.json().get('detail')}"
    )

    # 2. Non-existent plan check
    res = client.post("/subscriptions", json={"customer_id": customer_id, "plan_id": 999999})
    record_result(
        "Task 7",
        "POST /subscriptions (Non-existent Plan 404)",
        res.status_code == 404,
        f"Correctly returned 404: {res.json().get('detail')}"
    )

    # 3. Cannot subscribe to archived plan check
    res = client.post("/subscriptions", json={"customer_id": customer_id, "plan_id": arch_plan_id})
    record_result(
        "Task 7",
        "POST /subscriptions (Archived Plan 400)",
        res.status_code == 400,
        f"Correctly returned 400: {res.json().get('detail')}"
    )

    # 4. Standard Subscription Creation with Trial
    res = client.post("/subscriptions", json={"customer_id": customer_id, "plan_id": plan_id})
    if res.status_code == 201:
        sub_data = res.json()
        sub_id = sub_data["id"]
        status_ok = sub_data["status"] == "trial"
        trial_end_ok = sub_data["trial_end_date"] is not None
        record_result(
            "Task 7",
            "POST /subscriptions (Trial Period Assigned)",
            status_ok and trial_end_ok,
            f"Created Subscription #{sub_id} in 'trial' state with trial_end_date={sub_data['trial_end_date'][:10]}."
        )
    else:
        record_result("Task 7", "POST /subscriptions (Trial Period Assigned)", False, f"HTTP {res.status_code}: {res.text}")
        return

    # Verify initial BillingCycle created in database
    db = SessionLocal()
    try:
        cycle = db.query(BillingCycle).filter(BillingCycle.subscription_id == sub_id).first()
        cycle_ok = cycle is not None and cycle.status == "pending"
        record_result(
            "Task 7",
            "Verify Initial BillingCycle Created",
            cycle_ok,
            f"BillingCycle #{cycle.id if cycle else 'None'} found with status='{cycle.status if cycle else 'None'}'."
        )

        audit_create = (
            db.query(AuditLog)
            .filter(AuditLog.entity_type == "subscription", AuditLog.entity_id == sub_id, AuditLog.action.in_(["CREATE", "SUBSCRIPTION_CREATED"]))
            .first()
        )
        record_result(
            "Task 7",
            "Verify Subscription AuditLog Generated",
            audit_create is not None,
            f"Audit log #{audit_create.id if audit_create else 'None'} verified."
        )
    finally:
        db.close()

    # 5. Immediate Active Subscription Creation (start_immediately_active=True)
    res = client.post("/subscriptions", json={
        "customer_id": customer_id,
        "plan_id": secondary_plan_id,
        "start_immediately_active": True
    })
    imm_sub_id = res.json()["id"]
    imm_status = res.json()["status"]
    imm_activated = res.json()["activated_at"]
    record_result(
        "Task 7",
        "POST /subscriptions (Immediate Activation)",
        res.status_code == 201 and imm_status == "active" and imm_activated is not None,
        f"Created Subscription #{imm_sub_id} immediately active without trial."
    )

    # 6. Verify cannot delete plan while active subscriptions depend on it
    res_del_fail = client.delete(f"/plans/{plan_id}")
    record_result(
        "Task 5",
        "DELETE /plans/{id} Blocked with Active Subscriptions",
        res_del_fail.status_code == 400,
        f"Protected from accidental deletion: {res_del_fail.json().get('detail')}"
    )

    # ----------------------------------------------------
    # TASK 8: SUBSCRIPTION LIFECYCLE OPERATIONS
    # ----------------------------------------------------
    print("\n--- Testing Task 8: Subscription Lifecycle Operations ---")

    # 1. POST /subscriptions/{id}/pause (From Trial or Active -> Paused)
    res = client.post(f"/subscriptions/{sub_id}/pause")
    paused_data = res.json()
    record_result(
        "Task 8",
        "POST /subscriptions/{id}/pause",
        res.status_code == 200 and paused_data["status"] == "paused" and paused_data["paused_at"] is not None,
        f"Subscription #{sub_id} successfully transitioned to 'paused'."
    )

    # Attempt double pause -> 400
    res_double_pause = client.post(f"/subscriptions/{sub_id}/pause")
    record_result(
        "Task 8",
        "POST /subscriptions/{id}/pause (Double Pause Blocked)",
        res_double_pause.status_code == 400,
        f"Rejected: {res_double_pause.json().get('detail')}"
    )

    # 2. POST /subscriptions/{id}/resume (From Paused -> Active)
    res = client.post(f"/subscriptions/{sub_id}/resume")
    resumed_data = res.json()
    record_result(
        "Task 8",
        "POST /subscriptions/{id}/resume",
        res.status_code == 200 and resumed_data["status"] == "active" and resumed_data["activated_at"] is not None,
        f"Subscription #{sub_id} successfully resumed to 'active'."
    )

    # Attempt resume when already active -> 400
    res_invalid_resume = client.post(f"/subscriptions/{sub_id}/resume")
    record_result(
        "Task 8",
        "POST /subscriptions/{id}/resume (Invalid State Blocked)",
        res_invalid_resume.status_code == 400,
        f"Rejected: {res_invalid_resume.json().get('detail')}"
    )

    # 3. POST /subscriptions/{id}/change-plan
    res = client.post(f"/subscriptions/{sub_id}/change-plan", json={"new_plan_id": secondary_plan_id})
    changed_data = res.json()
    record_result(
        "Task 8",
        "POST /subscriptions/{id}/change-plan",
        res.status_code == 200 and changed_data["plan_id"] == secondary_plan_id,
        f"Subscription #{sub_id} switched to Plan #{secondary_plan_id} ('{changed_data.get('plan_name')}')."
    )

    # Attempt change to archived plan -> 400
    res_change_arch = client.post(f"/subscriptions/{sub_id}/change-plan", json={"new_plan_id": arch_plan_id})
    record_result(
        "Task 8",
        "POST /subscriptions/{id}/change-plan (Archived Plan Blocked)",
        res_change_arch.status_code == 400,
        f"Rejected: {res_change_arch.json().get('detail')}"
    )

    # 4. POST /subscriptions/{id}/cancel
    res = client.post(f"/subscriptions/{sub_id}/cancel")
    cancelled_data = res.json()
    record_result(
        "Task 8",
        "POST /subscriptions/{id}/cancel",
        res.status_code == 200 and cancelled_data["status"] == "cancelled" and cancelled_data["cancelled_at"] is not None,
        f"Subscription #{sub_id} cancelled. Terminal state reached."
    )

    # Verify pending billing cycles marked completed
    db = SessionLocal()
    try:
        pending_cycles = (
            db.query(BillingCycle)
            .filter(BillingCycle.subscription_id == sub_id, BillingCycle.status == "pending")
            .count()
        )
        record_result(
            "Task 8",
            "Verify BillingCycles Completed on Cancel",
            pending_cycles == 0,
            "All pending billing cycles automatically marked 'completed' upon cancellation."
        )

        audit_actions = [
            a.action for a in db.query(AuditLog).filter(AuditLog.entity_id == sub_id).all()
        ]
        has_pause_audit = "PAUSE_SUBSCRIPTION" in audit_actions or "SUBSCRIPTION_PAUSED" in audit_actions
        has_resume_audit = "RESUME_SUBSCRIPTION" in audit_actions or "SUBSCRIPTION_RESUMED" in audit_actions
        has_plan_audit = "CHANGE_PLAN" in audit_actions or "PLAN_CHANGED" in audit_actions
        has_cancel_audit = "CANCEL_SUBSCRIPTION" in audit_actions or "SUBSCRIPTION_CANCELLED" in audit_actions
        record_result(
            "Task 8",
            "Verify Lifecycle Audit Logs Recorded",
            has_pause_audit and has_resume_audit and has_plan_audit and has_cancel_audit,
            f"Recorded audit actions: {audit_actions}"
        )
    finally:
        db.close()

    # Attempt operations on cancelled subscription -> 400
    res_post_cancel_pause = client.post(f"/subscriptions/{sub_id}/pause")
    res_post_cancel_plan = client.post(f"/subscriptions/{sub_id}/change-plan", json={"new_plan_id": plan_id})
    res_post_cancel_cancel = client.post(f"/subscriptions/{sub_id}/cancel")
    terminal_enforced = (
        res_post_cancel_pause.status_code == 400
        and res_post_cancel_plan.status_code == 400
        and res_post_cancel_cancel.status_code == 400
    )
    record_result(
        "Task 8",
        "Terminal State Lock (Cancelled Subscription Immutability)",
        terminal_enforced,
        "All mutation operations on cancelled subscription strictly rejected with HTTP 400."
    )

    # ----------------------------------------------------
    # FINAL SUMMARY REPORT
    # ----------------------------------------------------
    print("\n" + "=" * 80)
    print("TASKS 5, 6, 7 & 8 VERIFICATION SUMMARY TABLE")
    print("=" * 80)
    print(f"{'TASK':<10} | {'TEST DESCRIPTION':<45} | {'STATUS':<6} | {'DETAILS'}")
    print("-" * 80)
    total_passed = 0
    for r in test_results:
        if r["status"] == "PASS":
            total_passed += 1
        print(f"{r['task']:<10} | {r['test']:<45} | {r['status']:<6} | {r['notes']}")
    print("-" * 80)
    print(f"TOTAL: {total_passed}/{len(test_results)} PASSED ({(total_passed/len(test_results))*100:.1f}%)")
    print("=" * 80)

    assert total_passed == len(test_results), f"Some tests failed! {len(test_results) - total_passed} failures."


if __name__ == "__main__":
    run_full_verification()
