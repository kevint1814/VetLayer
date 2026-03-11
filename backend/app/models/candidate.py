"""
Candidate model — represents a parsed resume / applicant.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, Float
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # ── Basic info ───────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # ── Resume data ──────────────────────────────────────────────────
    resume_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    resume_raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_parsed: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    intelligence_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Experience summary ───────────────────────────────────────────
    years_experience: Mapped[float | None] = mapped_column(Float, nullable=True)
    education_level: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_role: Mapped[str | None] = mapped_column("current_role", String(300), nullable=True)
    current_company: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # ── Processing status ────────────────────────────────────────────
    processing_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default="pending"
    )  # "pending", "parsing", "generating_profile", "ready", "failed"

    # ── Metadata ─────────────────────────────────────────────────────
    source: Mapped[str | None] = mapped_column(String(100), nullable=True, default="upload")  # "upload", "api", "synthetic"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    # ── Relationships ────────────────────────────────────────────────
    skills = relationship("Skill", back_populates="candidate", cascade="all, delete-orphan")
    analysis_results = relationship("AnalysisResult", back_populates="candidate", cascade="all, delete-orphan")
