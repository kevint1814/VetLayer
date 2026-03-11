"""
Pydantic schemas for analysis endpoints.
"""

import uuid
from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, ConfigDict


class AnalysisTriggerRequest(BaseModel):
    candidate_id: uuid.UUID
    job_id: uuid.UUID


class AnalysisTriggerResponse(BaseModel):
    analysis_id: uuid.UUID
    status: str


class RiskFlagResponse(BaseModel):
    id: uuid.UUID
    flag_type: str
    severity: str
    title: str
    description: str
    evidence: Optional[str] = None
    suggestion: Optional[str] = None
    is_dismissed: bool = False

    model_config = ConfigDict(from_attributes=True)


class InterviewQuestionResponse(BaseModel):
    id: uuid.UUID
    category: str
    question: str
    rationale: str
    target_skill: Optional[str] = None
    expected_depth: Optional[int] = None
    priority: int = 5
    follow_ups: Optional[Any] = None

    model_config = ConfigDict(from_attributes=True)


class AnalysisResponse(BaseModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    overall_score: float
    skill_match_score: float
    experience_score: float
    education_score: float
    depth_score: float
    skill_breakdown: Optional[Any] = None
    strengths: Optional[Any] = None
    gaps: Optional[Any] = None
    summary_text: Optional[str] = None
    recommendation: Optional[str] = None
    recruiter_override: Optional[str] = None
    recruiter_notes: Optional[str] = None
    is_overridden: bool = False
    risk_flags: List[RiskFlagResponse] = []
    interview_questions: List[InterviewQuestionResponse] = []
    llm_model_used: Optional[str] = None
    processing_time_ms: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
