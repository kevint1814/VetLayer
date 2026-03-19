"""
Auth and user Pydantic schemas.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ── Auth request/response ────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    force_password_change: bool = False
    user: "UserResponse"


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=4)


# ── User schemas ─────────────────────────────────────────────────────

class UserResponse(BaseModel):
    id: str
    username: str
    full_name: str
    role: str
    is_active: bool
    force_password_change: bool
    last_login_at: Optional[datetime] = None
    failed_login_attempts: int = 0
    created_at: datetime
    company_id: Optional[str] = None
    company_name: Optional[str] = None

    class Config:
        from_attributes = True


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=4, max_length=50, pattern=r"^[a-zA-Z0-9_]+$")
    full_name: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=4)
    role: str = Field(default="recruiter", pattern=r"^(super_admin|company_admin|recruiter)$")
    company_id: Optional[str] = None


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=200)


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=4)


class UserListResponse(BaseModel):
    users: list[UserResponse]
    total: int


# ── Audit log schemas ────────────────────────────────────────────────

class AuditLogResponse(BaseModel):
    id: str
    username: str
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    details: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogListResponse(BaseModel):
    logs: list[AuditLogResponse]
    total: int


# ── Platform stats ───────────────────────────────────────────────────

class PlatformStatsResponse(BaseModel):
    total_users: int
    active_users: int
    total_candidates: int
    total_jobs: int
    total_analyses: int
    total_batch_runs: int
    recent_logins_7d: int


class CompanyResponse(BaseModel):
    id: str
    name: str
    slug: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class CreateCompanyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$")


# Resolve forward references
TokenResponse.model_rebuild()
