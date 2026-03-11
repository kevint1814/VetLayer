"""
Audit logging service — records user actions to the audit_logs table.
"""

import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)


async def log_action(
    db: AsyncSession,
    user: Optional[User],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[str] = None,
    ip_address: Optional[str] = None,
    username_override: Optional[str] = None,
) -> None:
    """
    Write an immutable audit log entry.

    For login failures where no user object is available,
    pass username_override instead.
    """
    try:
        entry = AuditLog(
            user_id=user.id if user else None,
            username=username_override or (user.username if user else "system"),
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            ip_address=ip_address,
        )
        db.add(entry)
        # Don't commit here — let the route's session lifecycle handle it
    except Exception as e:
        # Log the error but also re-raise for critical audit actions
        logger.error(f"Failed to write audit log: {e}")
        # For security-critical actions, we want to know if audit logging fails
        # but we don't want to break the user's operation, so we log loudly
        # but don't re-raise. The entry just won't be recorded.
        # In production, this should trigger an alert.
