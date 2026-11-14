"""
Centralized Audit Logging Service.
Provides unified, immutable recording of all critical lifecycle and system events.
"""

from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models import AuditLog, utc_now


def log_system_event(
    db: Session,
    entity_type: str,
    entity_id: int,
    action: str,
    old_value: Optional[Any] = None,
    new_value: Optional[Any] = None,
    performed_by: Optional[str] = None,
) -> AuditLog:
    """
    Creates and persists an immutable audit log entry in PostgreSQL.
    """
    now = utc_now()
    audit_entry = AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        old_value=old_value,
        new_value=new_value,
        performed_by=performed_by or "system",
        created_at=now,
    )
    db.add(audit_entry)
    db.commit()
    db.refresh(audit_entry)
    return audit_entry
