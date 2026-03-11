"""
Job model — represents a job description / role requirements.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    company: Mapped[str | None] = mapped_column(String(300), nullable=True)
    department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # ── Structured requirements (extracted or manually defined) ──────
    required_skills: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # e.g. [{"skill": "Python", "min_depth": 3, "weight": 0.8}, ...]

    preferred_skills: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    experience_range: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # e.g. {"min_years": 3, "max_years": 8}

    education_requirements: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    remote_policy: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Metadata ─────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    # ── Relationships ────────────────────────────────────────────────
    analysis_results = relationship("AnalysisResult", back_populates="job", cascade="all, delete-orphan")
