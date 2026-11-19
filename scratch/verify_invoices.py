"""
Verification Suite for Billing & Invoices Module (API, PDF Generation, Filters, and Dashboard UI).
"""

import re
import uuid
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import AuditLog, Invoice

client = TestClient(app)


def run_verification():
    run_id = uuid.uuid4().hex[:6]
    print("=" * 80)
    print(f"RUNNING INVOICE GENERATION & PDF VERIFICATION SUITE (Run ID: {run_id})")
    print("=" * 80)

    # 1. Setup: Create Plan, Customer, and Subscription (which creates a pending BillingCycle)
    plan_res = client.post("/plans", json={
        "name": f"Enterprise Pro {run_id}",
        "price": 200.0,
        "billing_interval": "monthly",
        "trial_days": 0,
        "features": {"priority_support": True},
    })
    assert plan_res.status_code == 201, f"Plan creation failed: {plan_res.text}"
    plan_id = plan_res.json()["id"]

    cust_res = client.post("/customers", json={
        "name": f"Elena Vance {run_id}",
        "email": f"elena.vance_{run_id}@orbital.io",
        "company_name": "Orbital Cloud Systems",
    })
    assert cust_res.status_code == 201, f"Customer creation failed: {cust_res.text}"
    customer_id = cust_res.json()["id"]

    sub_res = client.post("/subscriptions", json={
        "customer_id": customer_id,
        "plan_id": plan_id,
        "start_immediately_active": True,
    })
    assert sub_res.status_code == 201, f"Subscription creation failed: {sub_res.text}"
    sub_id = sub_res.json()["id"]

    # 2. POST /api/invoices/generate
    gen_res = client.post("/api/invoices/generate")
    assert gen_res.status_code == 201, f"Generate invoices failed: {gen_res.text}"
    gen_data = gen_res.json()
    assert gen_data["generated_count"] >= 1, f"Expected >= 1 generated invoice, got: {gen_data}"

    target_inv = next((i for i in gen_data["invoices"] if i["subscription_id"] == sub_id), None)
    assert target_inv is not None, "Newly created subscription did not receive an invoice!"

    inv_id = target_inv["id"]
    inv_num = target_inv["invoice_number"]
    assert re.match(r"^INV-\d{8}-\d{4}$", inv_num), f"Invalid invoice number format: {inv_num}"
    assert target_inv["subtotal"] == 200.0
    assert target_inv["tax_amount"] == 36.0
    assert target_inv["total_amount"] == 236.0
    assert target_inv["status"] == "unpaid"
    assert target_inv["customer_name"] == f"Elena Vance {run_id}"
    assert target_inv["plan_name"] == f"Enterprise Pro {run_id}"
    print(f"[PASS] POST /api/invoices/generate -> Created {inv_num} (Total: $236.00, status=unpaid)")

    # Verify Audit Log entry
    db = SessionLocal()
    try:
        audit_row = (
            db.query(AuditLog)
            .filter(
                AuditLog.entity_type == "invoice",
                AuditLog.entity_id == inv_id,
                AuditLog.action == "INVOICE_GENERATED",
            )
            .first()
        )
        assert audit_row is not None, "Expected INVOICE_GENERATED audit log entry!"
        print(f"[PASS] AuditLog verified -> #{audit_row.id} ({audit_row.action})")
    finally:
        db.close()

    # Verify idempotency: running generate again should NOT duplicate the same cycle's invoice
    gen_res_2 = client.post("/api/invoices/generate")
    assert gen_res_2.status_code == 201
    gen_data_2 = gen_res_2.json()
    dup_inv = next((i for i in gen_data_2["invoices"] if i["subscription_id"] == sub_id), None)
    assert dup_inv is None, "Duplicate invoice generated for an already invoiced cycle!"
    print("[PASS] POST /api/invoices/generate idempotency verified (0 duplicates)")

    # 3. GET /api/invoices with search & status filters
    list_res = client.get(f"/api/invoices?search={inv_num}&status=unpaid")
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) == 1 and items[0]["id"] == inv_id
    print(f"[PASS] GET /api/invoices?search={inv_num}&status=unpaid -> Exact match returned")

    # Partial date search (YYYY-MM-DD)
    date_part = target_inv["invoice_date"][:10]
    date_res = client.get(f"/api/invoices?search={date_part}")
    assert date_res.status_code == 200
    assert any(i["id"] == inv_id for i in date_res.json())
    print(f"[PASS] GET /api/invoices?search={date_part} -> Partial date match verified")

    # Test status=paid filter
    db = SessionLocal()
    try:
        db_inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
        db_inv.status = "paid"
        db.commit()
    finally:
        db.close()

    paid_res = client.get(f"/api/invoices?search={inv_num}&status=paid")
    assert paid_res.status_code == 200 and len(paid_res.json()) == 1
    unpaid_res = client.get(f"/api/invoices?search={inv_num}&status=unpaid")
    assert unpaid_res.status_code == 200 and len(unpaid_res.json()) == 0
    print("[PASS] GET /api/invoices?status=paid vs unpaid filtering verified")

    # 4. GET /api/invoices/{id}
    detail_res = client.get(f"/api/invoices/{inv_id}")
    assert detail_res.status_code == 200, f"Detail endpoint failed: {detail_res.text}"
    detail = detail_res.json()
    assert detail["customer_email"] == f"elena.vance_{run_id}@orbital.io"
    assert detail["customer_company"] == "Orbital Cloud Systems"
    assert detail["plan_name"] == f"Enterprise Pro {run_id}"
    assert len(detail["line_items"]) == 1
    assert detail["line_items"][0]["amount"] == 200.0
    print(f"[PASS] GET /api/invoices/{inv_id} -> Joined Customer, Plan, and Line Items verified")

    assert client.get("/api/invoices/999999").status_code == 404
    print("[PASS] GET /api/invoices/999999 -> 404 Not Found verified")

    # 5. GET /api/invoices/{id}/pdf
    pdf_res = client.get(f"/api/invoices/{inv_id}/pdf")
    assert pdf_res.status_code == 200, f"PDF endpoint failed: {pdf_res.text}"
    assert pdf_res.headers.get("content-type") == "application/pdf"
    content_disp = pdf_res.headers.get("content-disposition", "")
    assert "attachment" in content_disp and f"{inv_num}.pdf" in content_disp
    assert pdf_res.content.startswith(b"%PDF-"), "Response is not a valid PDF document!"
    assert len(pdf_res.content) > 1000, f"PDF too small ({len(pdf_res.content)} bytes)"
    print(f"[PASS] GET /api/invoices/{inv_id}/pdf -> Valid PDF streamed ({len(pdf_res.content)} bytes, attachment={inv_num}.pdf)")

    assert client.get("/api/invoices/999999/pdf").status_code == 404

    # 6. Dashboard UI & OpenAPI Checks
    home_res = client.get("/home")
    assert home_res.status_code == 200
    html = home_res.text
    for expected in [
        'data-tab="overview"',
        'data-tab="customers"',
        'data-tab="invoices"',
        'id="invoiceSearchInput"',
        'id="invoiceStatusFilter"',
        "/api/invoices",
        "Download PDF",
    ]:
        assert expected in html, f"Missing expected UI element in /home: {expected}"
    assert not re.search(r"Milestone|Task\s*\d+", html, re.IGNORECASE), "Found prohibited label in /home HTML!"
    print("[PASS] Dashboard /home sidebar, Invoices tab, filters, PDF button, and clean terminology verified")

    openapi = client.get("/openapi.json").json()
    paths = openapi.get("paths", {})
    for ep in ["/api/invoices/generate", "/api/invoices", "/api/invoices/{id}", "/api/invoices/{id}/pdf"]:
        assert ep in paths, f"Missing OpenAPI path: {ep}"
    openapi_str = str(openapi)
    assert not re.search(r"Milestone\s*\d*|Task\s*\d+", openapi_str, re.IGNORECASE), "Found prohibited label in OpenAPI schema!"
    print("[PASS] OpenAPI schema paths and professional tags verified")

    print("=" * 80)
    print("ALL BILLING & INVOICES TESTS PASSED 100%!")
    print("=" * 80)


if __name__ == "__main__":
    run_verification()
