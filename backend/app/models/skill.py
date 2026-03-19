"""
Skill and SkillEvidence models — the heart of the Skill→Evidence→Depth pipeline.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, Integer, Float, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.core.database import Base



class DepthLevel(enum.IntEnum):
    """Skill depth estimation scale (1-5)."""
    AWARENESS = 1       # Mentioned but no real evidence
    BEGINNER = 2        # Basic usage, coursework, tutorials
    INTERMEDIATE = 3    # Professional use with concrete outputs
    ADVANCED = 4        # Deep expertise, leadership, optimization
    EXPERT = 5          # Industry-recognized, published, invented


class EvidenceType(str, enum.Enum):
    """Types of evidence that support a skill claim."""
    WORK_EXPERIENCE = "work_experience"
    PROJECT = "project"
    CERTIFICATION = "certification"
    EDUCATION = "education"
    PUBLICATION = "publication"
    OPEN_SOURCE = "open_source"
    AWARD = "award"
    SELF_REPORTED = "self_reported"


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # e.g. "programming_language", "framework", "soft_skill", "domain"

    # ── Depth assessment ─────────────────────────────────────────────
    estimated_depth: Mapped[int] = mapped_column(Integer, default=1)  # 1-5
    depth_confidence: Mapped[float] = mapped_column(Float, default=0.5)  # 0.0-1.0
    depth_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Recency & duration ───────────────────────────────────────────
    last_used_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    years_of_use: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Metadata ─────────────────────────────────────────────────────
    raw_mentions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Stores the exact text spans where this skill was found

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # ── Relationships ────────────────────────────────────────────────
    candidate = relationship("Candidate", back_populates="skills")
    evidence = relationship("SkillEvidence", back_populates="skill", cascade="all, delete-orphan")


class SkillEvidence(Base):
    __tablename__ = "skill_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id"), nullable=False, index=True
    )
    evidence_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The exact excerpt from the resume

    strength: Mapped[float] = mapped_column(Float, default=0.5)  # 0.0-1.0
    # How strongly this evidence supports the skill claim

    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Additional structured info: company name, dates, project details

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # ── Relationships ────────────────────────────────────────────────
    skill = relationship("Skill", back_populates="evidence")
