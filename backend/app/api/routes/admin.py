"""
Admin routes — user management, audit logs, platform stats.
All routes require admin role.
"""

import logging
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    hash_password,
    validate_password_strength,
    get_current_admin,
    get_current_super_admin,
    get_client_ip,
)
from app.models.user import User
from app.models.audit_log import AuditLog
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.analysis import AnalysisResult, BatchAnalysis
from app.models.company import Company
from app.schemas.auth import (
    UserResponse,
    UserListResponse,
    CreateUserRequest,
    ResetPasswordRequest,
    AuditLogResponse,
    AuditLogListResponse,
    PlatformStatsResponse,
    CompanyResponse,
    CreateCompanyRequest,
)
from app.services.audit import log_action

logger = logging.getLogger(__name__)
router = APIRouter()


def _parse_user_id(user_id: str) -> uuid_mod.UUID:
    """Validate and parse a user_id path parameter as UUID."""
    try:
        return uuid_mod.UUID(user_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid user ID format")


async def _get_user_or_404(db: AsyncSession, user_id: str) -> User:
    """Look up a user by ID string, raising 404 if not found."""
    from sqlalchemy.orm import selectinload
    uid = _parse_user_id(user_id)
    result = await db.execute(
        select(User).where(User.id == uid).options(selectinload(User.company))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ── User Management ──────────────────────────────────────────────────

@router.get("/users", response_model=UserListResponse)
async def list_users(
    role: Optional[str] = Query(None, pattern=r"^(super_admin|company_admin|recruiter)$"),
    status: Optional[str] = Query(None, pattern=r"^(active|inactive|pending)$"),
    search: Optional[str] = Query(None),
    company_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """List users with optional filters. super_admin sees all; company_admin sees only their company."""
    query = select(User)
    count_query = select(func.count(User.id))

    # If company_admin, restrict to their company only
    if admin.role == "company_admin":
        query = query.where(User.company_id == admin.company_id)
        count_query = count_query.where(User.company_id == admin.company_id)
    # If super_admin and company_id filter provided, apply it
    elif admin.role == "super_admin" and company_id:
        try:
            company_uuid = uuid_mod.UUID(company_id)
            query = query.where(User.company_id == company_uuid)
            count_query = count_query.where(User.company_id == company_uuid)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="Invalid company_id format")

    # Filters
    if role:
        query = query.where(User.role == role)
        count_query = count_query.where(User.role == role)
    if status == "active":
        query = query.where(User.is_active == True)
        count_query = count_query.where(User.is_active == True)
    elif status == "inactive":
        query = query.where(User.is_active == False)
        count_query = count_query.where(User.is_active == False)
    elif status == "pending":
        query = query.where(and_(User.is_active == True, User.force_password_change == True))
        count_query = count_query.where(and_(User.is_active == True, User.force_password_change == True))
    if search:
        pattern = f"%{search}%"
        query = query.where(
            User.username.ilike(pattern) | User.full_name.ilike(pattern)
        )
        count_query = count_query.where(
            User.username.ilike(pattern) | User.full_name.ilike(pattern)
        )

    from sqlalchemy.orm import selectinload
    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(User.created_at.desc()).offset(skip).limit(limit)
        .options(selectinload(User.company))
    )
    users = result.scalars().all()

    return UserListResponse(
        users=[
            UserResponse(
                id=str(u.id), username=u.username, full_name=u.full_name,
                role=u.role, is_active=u.is_active,
                force_password_change=u.force_password_change,
                last_login_at=u.last_login_at,
                failed_login_attempts=u.failed_login_attempts,
                created_at=u.created_at,
                company_id=str(u.company_id) if u.company_id else None,
                company_name=u.company.name if u.company else None,
            )
            for u in users
        ],
        total=total,
    )


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    body: CreateUserRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Create a new user account (admin only).
    super_admin: can create any role, requires company_id for non-super_admin roles
    company_admin: can only create 'recruiter' role, auto-sets to own company
    """
    # Validate password
    error = validate_password_strength(body.password)
    if error:
        raise HTTPException(status_code=400, detail=error)

    # Role validation for company_admin
    if admin.role == "company_admin":
        if body.role != "recruiter":
            raise HTTPException(status_code=403, detail="company_admin can only create recruiter accounts")
        company_id = admin.company_id  # Auto-set to admin's company
    elif admin.role == "super_admin":
        # super_admin can create any role but must provide company_id for non-super_admin
        if body.role != "super_admin" and not body.company_id:
            raise HTTPException(status_code=400, detail="company_id required for non-super_admin roles")
        try:
            company_id = uuid_mod.UUID(body.company_id) if body.company_id else None
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid company_id format")
    else:
        # Legacy "admin" role treated as super_admin
        if body.role != "super_admin" and not body.company_id:
            raise HTTPException(status_code=400, detail="company_id required for non-super_admin roles")
        try:
            company_id = uuid_mod.UUID(body.company_id) if body.company_id else None
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid company_id format")

    user = User(
        username=body.username,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role=body.role,
        company_id=company_id,
        is_active=True,
        force_password_change=True,  # Must change on first login
    )
    db.add(user)

    # Handle race condition: if two admins create same username simultaneously
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Username already exists")

    await log_action(
        db, admin, "create_user",
        target_type="user", target_id=str(user.id),
        details=f"Created {user.role} account: {user.username}",
        ip_address=get_client_ip(request),
    )

    # Look up company name without lazy-loading the relationship on a new object
    company_name = None
    if user.company_id:
        from app.models.company import Company as CompanyModel
        c_result = await db.execute(select(CompanyModel.name).where(CompanyModel.id == user.company_id))
        c_row = c_result.first()
        company_name = c_row[0] if c_row else None

    return UserResponse(
        id=str(user.id), username=user.username, full_name=user.full_name,
        role=user.role, is_active=user.is_active,
        force_password_change=user.force_password_change,
        last_login_at=user.last_login_at,
        failed_login_attempts=user.failed_login_attempts,
        created_at=user.created_at,
        company_id=str(user.company_id) if user.company_id else None,
        company_name=company_name,
    )


@router.get("/users/check-username")
async def check_username(
    username: str = Query(..., min_length=4, max_length=50),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Check if a username is available."""
    existing = await db.execute(select(User).where(User.username == username))
    return {"available": existing.scalar_one_or_none() is None}


@router.post("/users/{user_id}/deactivate", response_model=UserResponse)
async def deactivate_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Deactivate a user account (soft disable).
    company_admin can only deactivate users in their company.
    """
    user = await _get_user_or_404(db, user_id)
    if str(user.id) == str(admin.id):
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    # company_admin scope check
    if admin.role == "company_admin" and user.company_id != admin.company_id:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    await log_action(
        db, admin, "deactivate_user",
        target_type="user", target_id=str(user.id),
        details=f"Deactivated account: {user.username}",
        ip_address=get_client_ip(request),
    )

    return UserResponse(
        id=str(user.id), username=user.username, full_name=user.full_name,
        role=user.role, is_active=user.is_active,
        force_password_change=user.force_password_change,
        last_login_at=user.last_login_at,
        failed_login_attempts=user.failed_login_attempts,
        created_at=user.created_at,
        company_id=str(user.company_id) if user.company_id else None,
        company_name=user.company.name if user.company else None,
    )


@router.post("/users/{user_id}/reactivate", response_model=UserResponse)
async def reactivate_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Reactivate a previously deactivated user account.
    company_admin can only reactivate users in their company.
    """
    user = await _get_user_or_404(db, user_id)

    # company_admin scope check
    if admin.role == "company_admin" and user.company_id != admin.company_id:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = True
    user.failed_login_attempts = 0
    user.locked_until = None  # Also clear any lockout
    await log_action(
        db, admin, "reactivate_user",
        target_type="user", target_id=str(user.id),
        details=f"Reactivated account: {user.username}",
        ip_address=get_client_ip(request),
    )

    return UserResponse(
        id=str(user.id), username=user.username, full_name=user.full_name,
        role=user.role, is_active=user.is_active,
        force_password_change=user.force_password_change,
        last_login_at=user.last_login_at,
        failed_login_attempts=user.failed_login_attempts,
        created_at=user.created_at,
        company_id=str(user.company_id) if user.company_id else None,
        company_name=user.company.name if user.company else None,
    )


@router.post("/users/{user_id}/reset-password", response_model=UserResponse)
async def reset_user_password(
    user_id: str,
    body: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Reset a user's password (admin only). Forces password change on next login.
    company_admin can only reset passwords for users in their company.
    """
    user = await _get_user_or_404(db, user_id)

    # company_admin scope check
    if admin.role == "company_admin" and user.company_id != admin.company_id:
        raise HTTPException(status_code=404, detail="User not found")

    error = validate_password_strength(body.new_password)
    if error:
        raise HTTPException(status_code=400, detail=error)

    user.hashed_password = hash_password(body.new_password)
    user.force_password_change = True
    user.failed_login_attempts = 0
    user.locked_until = None  # Clear any lockout on admin reset

    await log_action(
        db, admin, "reset_password",
        target_type="user", target_id=str(user.id),
        details=f"Password reset for: {user.username}",
        ip_address=get_client_ip(request),
    )

    return UserResponse(
        id=str(user.id), username=user.username, full_name=user.full_name,
        role=user.role, is_active=user.is_active,
        force_password_change=user.force_password_change,
        last_login_at=user.last_login_at,
        failed_login_attempts=user.failed_login_attempts,
        created_at=user.created_at,
        company_id=str(user.company_id) if user.company_id else None,
        company_name=user.company.name if user.company else None,
    )


# ── Audit Logs ───────────────────────────────────────────────────────

@router.get("/audit-logs", response_model=AuditLogListResponse)
async def get_audit_logs(
    username: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """List audit log entries with optional filters.
    super_admin sees all; company_admin sees only their company's logs.
    """
    query = select(AuditLog)
    count_query = select(func.count(AuditLog.id))

    # If company_admin, restrict to their company only
    if admin.role == "company_admin":
        query = query.where(AuditLog.company_id == admin.company_id)
        count_query = count_query.where(AuditLog.company_id == admin.company_id)

    if username:
        query = query.where(AuditLog.username == username)
        count_query = count_query.where(AuditLog.username == username)
    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if target_type:
        query = query.where(AuditLog.target_type == target_type)
        count_query = count_query.where(AuditLog.target_type == target_type)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            AuditLog.details.ilike(pattern) | AuditLog.username.ilike(pattern)
        )
        count_query = count_query.where(
            AuditLog.details.ilike(pattern) | AuditLog.username.ilike(pattern)
        )

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    )
    logs = result.scalars().all()

    return AuditLogListResponse(
        logs=[
            AuditLogResponse(
                id=str(l.id), username=l.username, action=l.action,
                target_type=l.target_type, target_id=l.target_id,
                details=l.details, ip_address=l.ip_address,
                created_at=l.created_at,
            )
            for l in logs
        ],
        total=total,
    )


# ── Platform Stats ───────────────────────────────────────────────────

@router.get("/stats", response_model=PlatformStatsResponse)
async def get_platform_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Get platform statistics.
    super_admin sees global stats; company_admin sees only their company's stats.
    """
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

    if admin.role == "company_admin":
        # Company-scoped stats
        total_users = (await db.execute(
            select(func.count(User.id)).where(User.company_id == admin.company_id)
        )).scalar() or 0
        active_users = (await db.execute(
            select(func.count(User.id)).where(
                and_(User.is_active == True, User.company_id == admin.company_id)
            )
        )).scalar() or 0
        total_candidates = (await db.execute(
            select(func.count(Candidate.id)).where(Candidate.company_id == admin.company_id)
        )).scalar() or 0
        total_jobs = (await db.execute(
            select(func.count(Job.id)).where(Job.company_id == admin.company_id)
        )).scalar() or 0
        total_analyses = (await db.execute(
            select(func.count(AnalysisResult.id)).where(AnalysisResult.company_id == admin.company_id)
        )).scalar() or 0
        total_batches = (await db.execute(
            select(func.count(BatchAnalysis.id)).where(BatchAnalysis.company_id == admin.company_id)
        )).scalar() or 0
        recent_logins = (await db.execute(
            select(func.count(AuditLog.id)).where(
                and_(
                    AuditLog.action == "login",
                    AuditLog.created_at >= seven_days_ago,
                    AuditLog.company_id == admin.company_id,
                )
            )
        )).scalar() or 0
    else:
        # super_admin sees global stats
        total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
        active_users = (await db.execute(
            select(func.count(User.id)).where(User.is_active == True)
        )).scalar() or 0
        total_candidates = (await db.execute(select(func.count(Candidate.id)))).scalar() or 0
        total_jobs = (await db.execute(select(func.count(Job.id)))).scalar() or 0
        total_analyses = (await db.execute(select(func.count(AnalysisResult.id)))).scalar() or 0
        total_batches = (await db.execute(select(func.count(BatchAnalysis.id)))).scalar() or 0
        recent_logins = (await db.execute(
            select(func.count(AuditLog.id)).where(
                and_(AuditLog.action == "login", AuditLog.created_at >= seven_days_ago)
            )
        )).scalar() or 0

    return PlatformStatsResponse(
        total_users=total_users,
        active_users=active_users,
        total_candidates=total_candidates,
        total_jobs=total_jobs,
        total_analyses=total_analyses,
        total_batch_runs=total_batches,
        recent_logins_7d=recent_logins,
    )


# ── Company Management (super_admin only) ────────────────────────────────

@router.get("/companies", response_model=list[CompanyResponse])
async def list_companies(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_super_admin),
):
    """List all companies (super_admin only)."""
    result = await db.execute(select(Company).order_by(Company.created_at.desc()))
    companies = result.scalars().all()
    return [
        CompanyResponse(
            id=str(c.id),
            name=c.name,
            slug=c.slug,
            is_active=c.is_active,
            created_at=c.created_at,
        )
        for c in companies
    ]


@router.post("/companies", response_model=CompanyResponse, status_code=201)
async def create_company(
    body: CreateCompanyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_super_admin),
):
    """Create a new company (super_admin only)."""
    company = Company(
        name=body.name,
        slug=body.slug,
        is_active=True,
    )
    db.add(company)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Company slug already exists")

    await log_action(
        db, admin, "create_company",
        target_type="company", target_id=str(company.id),
        details=f"Created company: {company.name} ({company.slug})",
        ip_address=get_client_ip(request),
    )

    return CompanyResponse(
        id=str(company.id),
        name=company.name,
        slug=company.slug,
        is_active=company.is_active,
        created_at=company.created_at,
    )


@router.put("/companies/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: str,
    body: CreateCompanyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_super_admin),
):
    """Update company name/status (super_admin only)."""
    try:
        company_uuid = uuid_mod.UUID(company_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid company ID format")

    result = await db.execute(select(Company).where(Company.id == company_uuid))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    company.name = body.name
    company.slug = body.slug

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Company slug already exists")

    await log_action(
        db, admin, "update_company",
        target_type="company", target_id=str(company.id),
        details=f"Updated company: {company.name}",
        ip_address=get_client_ip(request),
    )

    return CompanyResponse(
        id=str(company.id),
        name=company.name,
        slug=company.slug,
        is_active=company.is_active,
        created_at=company.created_at,
    )
