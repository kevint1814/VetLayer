"""
Analysis models — results of running a candidate through VetLayer's engines.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, Integer, Float, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.core.database import Base


class RiskSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnalysisResult(Base):
    """
    One analysis = one candidate evaluated against one job.
    """
    __tablename__ = "analysis_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False, index=True
    )

    # ── Capability scores ────────────────────────────────────────────
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    skill_match_score: Mapped[float] = mapped_column(Float, default=0.0)
    experience_score: Mapped[float] = mapped_column(Float, default=0.0)
    education_score: Mapped[float] = mapped_column(Float, default=0.0)
    depth_score: Mapped[float] = mapped_column(Float, default=0.0)

    # ── Detailed breakdown ───────────────────────────────────────────
    skill_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Per-skill match details: {"Python": {"required_depth": 3, "estimated_depth": 4, "match": true}, ...}

    strengths: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    gaps: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Summary ──────────────────────────────────────────────────────
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # "strong_yes", "yes", "maybe", "no", "strong_no"

    # ── Human override ───────────────────────────────────────────────
    recruiter_override: Mapped[str | None] = mapped_column(String(50), nullable=True)
    recruiter_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_overridden: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Metadata ─────────────────────────────────────────────────────
    llm_model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # ── Relationships ────────────────────────────────────────────────
    candidate = relationship("Candidate", back_populates="analysis_results")
    job = relationship("Job", back_populates="analysis_results")
    risk_flags = relationship("RiskFlag", back_populates="analysis", cascade="all, delete-orphan")
    interview_questions = relationship("InterviewQuestion", back_populates="analysis", cascade="all, delete-orphan")


class RiskFlag(Base):
    """Flags raised by the Risk Engine."""
    __tablename__ = "risk_flags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_results.id"), nullable=False, index=True
    )
    flag_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # e.g. "employment_gap", "skill_inflation", "inconsistent_timeline", "missing_evidence"

    severity: Mapped[str] = mapped_column(String(20), default="medium")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Recruiter can dismiss flags ──────────────────────────────────
    is_dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    dismissed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    analysis = relationship("AnalysisResult", back_populates="risk_flags")


class BatchAnalysis(Base):
    """
    Persisted batch analysis run — stores metadata so recruiters can revisit
    past batch results at any time, even after server restarts.
    """
    __tablename__ = "batch_analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)

    # ── What was analyzed ─────────────────────────────────────────────
    candidate_ids: Mapped[dict] = mapped_column(JSONB, nullable=False)  # list of UUID strings
    job_ids: Mapped[dict] = mapped_column(JSONB, nullable=False)        # list of UUID strings

    # ── Status & stats ────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(30), default="processing")
    # "processing", "completed", "partial_failure", "failed"
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    cached: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Results snapshot ──────────────────────────────────────────────
    results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Stores list of {candidate_id, candidate_name, job_id, job_title,
    #  analysis_id, overall_score, recommendation, cached, error}

    # ── Display metadata (denormalized for fast card rendering) ───────
    job_titles: Mapped[dict | None] = mapped_column(JSONB, nullable=True)   # list of job title strings
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_score: Mapped[float] = mapped_column(Float, default=0.0)
    top_recommendation: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class InterviewQuestion(Base):
    """Generated interview questions based on analysis."""
    __tablename__ = "interview_questions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_results.id"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    # "skill_verification", "gap_exploration", "depth_probe", "behavioral", "red_flag"

    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    # Why this question was generated — tied to specific evidence or gaps

    target_skill: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expected_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)  # 1=highest

    follow_ups: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Suggested follow-up questions based on possible answers

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    analysis = relationship("AnalysisResult", back_populates="interview_questions")
