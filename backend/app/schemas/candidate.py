"""
Pydantic schemas for candidate endpoints.
"""

import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, EmailStr


class CandidateCreate(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    resume_filename: str
    resume_raw_text: Optional[str] = None
    source: Optional[str] = "manual"


class CandidateResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    resume_filename: str
    years_experience: Optional[float] = None
    education_level: Optional[str] = None
    current_role: Optional[str] = None
    current_company: Optional[str] = None
    source: Optional[str] = None
    processing_status: Optional[str] = None
    resume_parsed: Optional[dict] = None
    intelligence_profile: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CandidateList(BaseModel):
    candidates: List[CandidateResponse]
    total: int
