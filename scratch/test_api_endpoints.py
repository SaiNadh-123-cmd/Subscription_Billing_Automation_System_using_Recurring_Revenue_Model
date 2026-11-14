import sys
import os

sys.path.insert(0, os.path.abspath("."))
import app

from fastapi.testclient import TestClient
from app.main import app as fastapi_app

client = TestClient(fastapi_app)

def test_full_api_suite():
    print("=" * 60)
    print("TESTING HTML UI ROUTES")
    print("=" * 60)
    
    # 1. UI Routes
    res = client.get("/signup")
    assert res.status_code == 200
    assert "Create your account" in res.text
    print("✓ GET /signup returned 200 OK with HTML content.")

    res = client.get("/signin")
    assert res.status_code == 200
    assert "Welcome back" in res.text
    print("✓ GET /signin returned 200 OK with HTML content.")

    res = client.get("/home")
    assert res.status_code == 200
    assert "SubBilling" in res.text
    print("✓ GET /home returned 200 OK with HTML content.")

    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "running"
    print("✓ GET /api/health returned 200 OK.")

    print("\n" + "=" * 60)
    print("TESTING AUTHENTICATION API ENDPOINTS")
    print("=" * 60)

    import uuid
    run_id = uuid.uuid4().hex[:6]
    test_email = f"sarah.connor_{run_id}@cyberdyne.io"

    # 2. Signup
    signup_payload = {
        "name": "Sarah Connor",
        "email": test_email,
        "password": "Password9988!!",
        "company_name": "Resistance Inc."
    }
    res = client.post("/api/auth/signup", json=signup_payload)
    assert res.status_code == 201, f"Signup failed: {res.text}"
    signup_data = res.json()
    assert "access_token" in signup_data
    assert signup_data["user"]["email"] == test_email
    token = signup_data["access_token"]
    print("✓ POST /api/auth/signup succeeded and issued JWT token.")

    # Duplicate email signup test
    res = client.post("/api/auth/signup", json=signup_payload)
    assert res.status_code == 400
    print("✓ Duplicate email rejection verified (HTTP 400).")

    # 3. Signin via JSON
    signin_payload = {
        "email": test_email,
        "password": "Password9988!!"
    }
    res = client.post("/api/auth/signin", json=signin_payload)
    assert res.status_code == 200
    assert "access_token" in res.json()
    print("✓ POST /api/auth/signin (JSON) verified.")

    # 4. Signin via Form data (OAuth2 compatible for Swagger UI)
    form_data = {
        "username": test_email,
        "password": "Password9988!!"
    }
    res = client.post(
        "/api/auth/signin",
        data=form_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert res.status_code == 200
    assert "access_token" in res.json()
    print("✓ POST /api/auth/signin (OAuth2 form-data) verified.")

    # Wrong password test
    res = client.post("/api/auth/signin", json={"email": test_email, "password": "WrongPassword"})
    assert res.status_code == 401
    print("✓ Invalid credentials rejection verified (HTTP 401).")

    # 5. GET /api/auth/me (Protected route)
    # Without token: should return 401
    res = client.get("/api/auth/me")
    assert res.status_code == 401
    print("✓ Protected /api/auth/me rejects unauthenticated requests (HTTP 401).")

    # With valid Bearer token
    headers = {"Authorization": f"Bearer {token}"}
    res = client.get("/api/auth/me", headers=headers)
    assert res.status_code == 200
    me_data = res.json()
    assert me_data["user"]["email"] == test_email
    assert len(me_data["subscriptions"]) >= 1
    sub = me_data["subscriptions"][0]
    sub_id = sub["id"]
    print(f"✓ GET /api/auth/me verified. Primary subscription #{sub_id} in state '{sub['status']}'.")

    print("\n" + "=" * 60)
    print("TESTING SUBSCRIPTION STATE MACHINE API")
    print("=" * 60)

    # 6. Check transitions endpoint
    res = client.get(f"/api/subscriptions/{sub_id}/transitions", headers=headers)
    assert res.status_code == 200
    trans_info = res.json()
    print(f"Allowed transitions for subscription #{sub_id} ('{trans_info['current_status']}'):", trans_info["allowed_transitions"])
    assert set(trans_info["allowed_transitions"]) == {"active", "cancelled", "paused"}

    # 7. Attempt illegal transition via API (trial -> past_due)
    res = client.post(
        f"/api/subscriptions/{sub_id}/transition",
        headers=headers,
        json={"new_status": "past_due"}
    )
    assert res.status_code == 400
    print("✓ Illegal transition 'trial' -> 'past_due' rejected by API with HTTP 400:")
    print("  Error message:", res.json()["detail"])

    # 8. Attempt valid transition via API (trial -> active)
    res = client.post(
        f"/api/subscriptions/{sub_id}/transition",
        headers=headers,
        json={"new_status": "active"}
    )
    assert res.status_code == 200
    updated_sub = res.json()
    assert updated_sub["status"] == "active"
    print(f"✓ Valid transition 'trial' -> 'active' succeeded via API. Next allowed: {updated_sub['allowed_transitions']}")

    # 9. Valid transition (active -> past_due)
    res = client.post(
        f"/api/subscriptions/{sub_id}/transition",
        headers=headers,
        json={"new_status": "past_due"}
    )
    assert res.status_code == 200
    assert res.json()["status"] == "past_due"
    print("✓ Valid transition 'active' -> 'past_due' succeeded via API.")

    # 10. Valid transition (past_due -> cancelled - terminal)
    res = client.post(
        f"/api/subscriptions/{sub_id}/transition",
        headers=headers,
        json={"new_status": "cancelled"}
    )
    assert res.status_code == 200
    cancelled_sub = res.json()
    assert cancelled_sub["status"] == "cancelled"
    assert len(cancelled_sub["allowed_transitions"]) == 0
    print("✓ Valid transition 'past_due' -> 'cancelled' succeeded via API. Terminal state confirmed.")

    # 11. Attempt transition from terminal cancelled -> active
    res = client.post(
        f"/api/subscriptions/{sub_id}/transition",
        headers=headers,
        json={"new_status": "active"}
    )
    assert res.status_code == 400
    print("✓ Forbidden transition out of terminal 'cancelled' state rejected with HTTP 400:")
    print("  Error message:", res.json()["detail"])

    # 12. Check audit logs via API
    res = client.get(f"/api/subscriptions/{sub_id}/audit-logs", headers=headers)
    assert res.status_code == 200
    logs = res.json()
    print(f"✓ GET /api/subscriptions/{sub_id}/audit-logs returned {len(logs)} audit entries.")
    for l in logs:
        print(f"  - Action: {l['action']} | {l['old_value']} -> {l['new_value']} | By: {l['performed_by']}")

    print("\n" + "=" * 60)
    print("ALL API & UI ENDPOINTS VERIFIED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    test_full_api_suite()
