"""
Audit logging service — records user actions to the audit_logs table.
"""

import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)

# Actions where an audit log failure should be treated as critical.
# These are security-sensitive — if we can't record them, something is very wrong.
CRITICAL_ACTIONS = {
    "login", "login_failed", "logout", "change_password",
    "create_user", "deactivate_user", "reactivate_user", "reset_password",
}


async def log_action(
    db: AsyncSession,
    user: Optional[User],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[str] = None,
    ip_address: Optional[str] = None,
    username_override: Optional[str] = None,
    company_id: Optional[str] = None,
) -> None:
    """
    Write an immutable audit log entry.

    For login failures where no user object is available,
    pass username_override instead.

    For security-critical actions (login, password changes, user management),
    failures are logged at CRITICAL level to ensure they trigger alerts.
    """
    try:
        # Extract company_id from user if not provided
        final_company_id = company_id or (str(user.company_id) if (user and user.company_id) else None)
        entry = AuditLog(
            user_id=user.id if user else None,
            company_id=final_company_id,
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
        if action in CRITICAL_ACTIONS:
            # CRITICAL: Security-sensitive action failed to audit.
            # Log at critical level so monitoring/alerting picks it up.
            logger.critical(
                f"SECURITY AUDIT FAILURE: Could not record '{action}' for "
                f"user={username_override or (user.username if user else 'unknown')}, "
                f"ip={ip_address}. Error: {e}",
                exc_info=True,
            )
        else:
            logger.error(f"Failed to write audit log for '{action}': {e}")
