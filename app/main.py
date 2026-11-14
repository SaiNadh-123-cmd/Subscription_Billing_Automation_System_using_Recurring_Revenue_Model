"""
Main FastAPI Application Entrypoint.
Initializes PostgreSQL tables, executes safe schema migrations,
and registers authentication, plans, customers, subscriptions, billing, audit, and UI page routers.
"""

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db, engine, run_migrations
from app import models
from app.routers import auth, plans, customers, subscriptions, billing, audit, pages

# Ensure base metadata tables exist and run safe non-destructive column migrations
models.Base.metadata.create_all(bind=engine)
run_migrations()

tags_metadata = [
    {
        "name": "Authentication",
        "description": "User registration, authentication, JWT token generation, and customer profile management.",
    },
    {
        "name": "Plans",
        "description": "Subscription plan catalog management, pricing tiers, features, and plan archiving.",
    },
    {
        "name": "Customers",
        "description": "Customer accounts, profile updates, and customer-associated subscriptions.",
    },
    {
        "name": "Subscriptions",
        "description": "Subscription creation and finite state machine operations (pause, resume, change-plan, cancel).",
    },
    {
        "name": "Billing",
        "description": "Recurring billing engine, cycle renewals, and draft invoice generation.",
    },
    {
        "name": "System",
        "description": "Centralized system audit logs and health monitoring.",
    },
]

app = FastAPI(
    title="Subscription Billing Automation API",
    description=(
        "Enterprise subscription billing engine featuring JWT authentication, "
        "recurring revenue state machine, automated invoicing, plan management, "
        "customer lifecycle operations, and interactive UI dashboards."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=tags_metadata,
)

# 1. Frontend UI Pages
app.include_router(pages.router)

# 2. Authentication Router
app.include_router(auth.router)

# 3. Plan Management Router (Primary path /plans, alias /api/plans)
app.include_router(plans.router, prefix="/plans")
app.include_router(plans.router, prefix="/api/plans", include_in_schema=False)

# 4. Customer Management Router (Primary path /customers, alias /api/customers)
app.include_router(customers.router, prefix="/customers")
app.include_router(customers.router, prefix="/api/customers", include_in_schema=False)

# 5. Subscription & Lifecycle Operations Router (Primary path /subscriptions, alias /api/subscriptions)
app.include_router(subscriptions.router, prefix="/subscriptions")
app.include_router(subscriptions.router, prefix="/api/subscriptions", include_in_schema=False)

# 6. Billing Cycle Engine Router (Primary path /billing, alias /api/billing)
app.include_router(billing.router, prefix="/billing")
app.include_router(billing.router, prefix="/api/billing", include_in_schema=False)

# 7. Audit Logging System Router (Primary path /audit-logs, alias /api/audit-logs)
app.include_router(audit.router, prefix="/audit-logs")
app.include_router(audit.router, prefix="/api/audit-logs", include_in_schema=False)


@app.get("/api/health", tags=["System"])
def health_check(db: Session = Depends(get_db)):
    """Health check validating PostgreSQL connectivity."""
    try:
        db.execute(text("SELECT 1"))
        db_status = "Successfully connected to PostgreSQL!"
    except Exception as e:
        db_status = f"Connection failed: {str(e)}"

    return {
        "status": "running",
        "database_status": db_status,
    }
