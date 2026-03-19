"""
AuditLog model — immutable record of user actions across the platform.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=True, index=True
    )
    username: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # e.g. "login", "login_failed", "upload_candidate", "run_analysis",
    #      "create_job", "delete_candidate", "batch_analysis", "create_user",
    #      "reset_password", "deactivate_user"

    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # e.g. "candidate", "job", "analysis", "user", "batch"

    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    # ── Relationship ─────────────────────────────────────────────────
    user = relationship("User", foreign_keys=[user_id])
