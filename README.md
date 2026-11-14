# Subscription Billing Automation System

Enterprise subscription billing automation engine featuring JWT authentication, recurring revenue state machine, automated invoicing, customer management, plan management, and interactive UI dashboards.

Built with **FastAPI**, **SQLAlchemy 2.0**, **PostgreSQL** (via pure-Python `pg8000`), and **Tailwind CSS**.

---

## Features & Implemented Architecture

### 1. Database Design (Task 2)
Complete relational billing schema in PostgreSQL:
- **`plans`**: Subscription plans with pricing, billing intervals (`monthly`/`yearly`), trial days, feature flags (JSON), and archiving support.
- **`customers`**: Customer profiles with secure password hashing (`pbkdf2_sha256`), company details, and role-based access.
- **`subscriptions`**: Core recurring subscriptions governed by a strict state machine with start/end/renewal/paused/cancelled timestamp tracking.
- **`billing_cycles`**: Recurring billing cycle windows (`pending`/`completed`) with renewal dates.
- **`invoices`**: Financial invoice records with subtotal, tax amount, total amount, and statuses (`draft`/`paid`/`unpaid`/`void`).
- **`payments`**: Payment transaction logs linking to invoices with external reference tracking and statuses (`pending`/`success`/`failed`/`refunded`).
- **`audit_logs`**: Immutable audit logs tracking entity modifications and state transitions across plans, customers, and subscriptions.

### 2. User Authentication (Task 3)
- Pure-Python password hashing with `pbkdf2_sha256` (no C-extensions).
- Signed JWT access tokens with standard claims (`sub`, `id`, `role`, `exp`).
- Responsive Tailwind CSS pages for `/signup`, `/signin`, and protected `/home` dashboard.
- OAuth2 Password Bearer flow compatible with Swagger UI documentation.

### 3. Subscription Status State Machine (Task 4 & 8)
Strict state transition validation with terminal state enforcement and automated audit logging:
- **Allowed States**: `trial`, `active`, `paused`, `past_due`, `cancelled`.
- **Valid Transitions**:
  - `trial` &rarr; `active`, `paused`, `cancelled`
  - `active` &rarr; `past_due`, `paused`, `cancelled`
  - `paused` &rarr; `active`, `cancelled`
  - `past_due` &rarr; `active`, `cancelled`
  - `cancelled` &rarr; *(Terminal state: strictly locked against any further mutations)*
- Prohibits invalid transitions (e.g. `trial` &rarr; `past_due` or `cancelled` &rarr; `active`) with HTTP 400 Bad Request.

### 4. Plan Management (Task 5)
Mounted at `/plans`:
- `POST /plans`: Create plan with duplicate name protection (HTTP 400).
- `GET /plans`: List active plans (supports `include_archived=true`).
- `GET /plans/{id}`: Retrieve plan details.
- `PUT /plans/{id}`: Update plan attributes and record audit logs.
- `PATCH /plans/{id}/archive`: Soft-archive plans to prevent new subscriptions while keeping existing ones active.
- `DELETE /plans/{id}`: Prevent accidental deletion when active subscriptions depend on it.

### 5. Customer Management (Task 6)
Mounted at `/customers`:
- `POST /customers`: Register new customer with duplicate email check and password hashing.
- `GET /customers/{id}`: Fetch customer profile with their full subscription history.
- `GET /customers`: List customers with pagination (`skip`, `limit`) and search filter.
- `PUT /customers/{id}`: Update customer profile details.

### 6. Subscription Provisioning & Lifecycle Operations (Task 7 & 8)
Mounted under `/subscriptions`:
- `POST /subscriptions`: Provision new subscriptions with trial period calculation or immediate activation, auto-generating initial `BillingCycle` records.
- `POST /subscriptions/{id}/change-plan`: Switch subscription tier to an active plan with duration recalculation.
- `POST /subscriptions/{id}/pause`: Temporarily halt subscription from `trial` or `active`.
- `POST /subscriptions/{id}/resume`: Reactivate a `paused` subscription.
- `POST /subscriptions/{id}/cancel`: Move to terminal `cancelled` state, set `end_date`, and complete pending billing cycles.
### 7.  Billing Cycle Engine & Audit Logging (Task 9 & 10)
Objective 1:
Generate billing cycle records.

Requirements:
Monthly plans create next renewal date
Annual plans create next renewal date
Store billing cycle history

Deliverable
Billing cycle calculation service.
objective 2:
Track every important action.
Log Events
Plan created
Plan updated
Customer created
Subscription created
Plan changed
Subscription cancelled

Deliverable
Audit logs automatically generated.

---

## Tech Stack
- **Backend**: FastAPI, Uvicorn, Pydantic v2
- **ORM & DB Driver**: SQLAlchemy 2.0, `pg8000` (Pure-Python PostgreSQL driver with zero DLL / C-extension requirements)
- **Security & Auth**: PyJWT, `passlib` (PBKDF2-SHA256)
- **Frontend**: Jinja2, Tailwind CSS, Responsive Vanilla JavaScript
- **Testing**: FastAPI TestClient, Pytest

---

## Getting Started

### 1. Clone the repository
```bash
git clone https://github.com/SaiNadh-123-cmd/Subscription_Billing_Automation_System_using_Recurring_Revenue_Model.git
cd Subscription_Billing_Automation_System_using_Recurring_Revenue_Model
```

### 2. Set up environment
Create a virtual environment and install dependencies:
```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure `.env`
Copy `.env.example` to `.env` and configure your database credentials:
```bash
cp .env.example .env
```
Ensure PostgreSQL is running and the database `subscription_db` exists:
```sql
CREATE DATABASE subscription_db;
```

### 4. Run the Application
```bash
uvicorn app.main:app --reload --port 8000
```
- Interactive Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
- Interactive Dashboard UI: [http://localhost:8000/home](http://localhost:8000/home)
- Signup Page: [http://localhost:8000/signup](http://localhost:8000/signup)
- Signin Page: [http://localhost:8000/signin](http://localhost:8000/signin)

---

## Running Verification Tests
Execute the comprehensive test suites:
```bash
# Verify Tasks 5, 6, 7 & 8:
python verify_tasks_5_to_8.py

# Verify UI and Auth endpoints:
python scratch/test_api_endpoints.py
```
