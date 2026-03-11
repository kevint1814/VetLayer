"""
Pydantic schemas for job endpoints.
"""

import uuid
from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, ConfigDict


class JobCreate(BaseModel):
    title: str
    company: Optional[str] = None
    department: Optional[str] = None
    description: str
    required_skills: Optional[List[dict]] = None
    preferred_skills: Optional[List[dict]] = None
    experience_range: Optional[dict] = None
    education_requirements: Optional[dict] = None
    location: Optional[str] = None
    remote_policy: Optional[str] = None


class JobResponse(BaseModel):
    id: uuid.UUID
    title: str
    company: Optional[str] = None
    department: Optional[str] = None
    description: str
    required_skills: Optional[Any] = None
    preferred_skills: Optional[Any] = None
    experience_range: Optional[Any] = None
    education_requirements: Optional[Any] = None
    location: Optional[str] = None
    remote_policy: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobUpdate(BaseModel):
    """Partial update — only include fields you want to change."""
    title: Optional[str] = None
    company: Optional[str] = None
    department: Optional[str] = None
    description: Optional[str] = None
    required_skills: Optional[List[dict]] = None
    preferred_skills: Optional[List[dict]] = None
    experience_range: Optional[dict] = None
    education_requirements: Optional[dict] = None
    location: Optional[str] = None
    remote_policy: Optional[str] = None


class JobList(BaseModel):
    jobs: List[JobResponse]
    total: int
