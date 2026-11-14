"""
Audit Trail & System Events Router.
Provides queryable access to immutable audit log records across all platform entities.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog
from app.schemas import AuditLogResponse

router = APIRouter(tags=["System"])


@router.get(
    "",
    response_model=List[AuditLogResponse],
    summary="List system audit events",
)
def list_audit_logs(
    skip: int = Query(0, ge=0, description="Number of logs to skip"),
    limit: int = Query(50, ge=1, le=500, description="Max logs to return"),
    entity_type: Optional[str] = Query(None, description="Filter by entity type (e.g. plan, customer, subscription)"),
    action: Optional[str] = Query(None, description="Filter by action (e.g. PLAN_CREATED, BILLING_CYCLE_GENERATED)"),
    db: Session = Depends(get_db),
):
    """
    Returns a paginated list of immutable system events, sorted by creation timestamp descending.
    """
    query = db.query(AuditLog)

    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type.strip().lower())
    if action:
        query = query.filter(AuditLog.action.ilike(f"%{action.strip()}%"))

    logs = query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()
    return logs
