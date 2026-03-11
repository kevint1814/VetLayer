"""
Authentication routes — login, refresh, logout, change password.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    verify_password,
    hash_password,
    validate_password_strength,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    get_client_ip,
)
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    ChangePasswordRequest,
    UserResponse,
)
from app.services.audit import log_action

logger = logging.getLogger(__name__)
router = APIRouter()


def _is_locked(user: User) -> bool:
    """Check if user account is currently locked due to too many failed attempts."""
    if user.locked_until and user.locked_until > datetime.now(timezone.utc):
        return True
    return False


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate with username and password. Returns access token + sets refresh cookie."""
    ip = get_client_ip(request)

    # Look up user
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    # ── Timing-safe check: always verify against a dummy hash if user not found
    # This prevents username enumeration via response timing differences.
    if not user:
        verify_password(body.password, "$2b$12$LJ3m4ys3Lg2JFYUqiF.OxOiHwPFMlsVEEfJ1P0OI0gJzXQfGjT0m2")
        await log_action(
            db, None, "login_failed",
            details=f"Invalid credentials for username: {body.username}",
            ip_address=ip,
            username_override=body.username,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # ── Account lockout check
    if _is_locked(user):
        await log_action(
            db, user, "login_failed",
            details="Account is locked due to too many failed attempts",
            ip_address=ip,
        )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account is temporarily locked due to too many failed login attempts. Please try again later or contact your administrator.",
        )

    # ── Deactivation check
    if not user.is_active:
        await log_action(
            db, user, "login_failed",
            details="Account is deactivated",
            ip_address=ip,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account has been deactivated. Contact your administrator.",
        )

    # ── Password verification
    if not verify_password(body.password, user.hashed_password):
        user.failed_login_attempts += 1
        # Lock account if threshold exceeded
        if user.failed_login_attempts >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
            from datetime import timedelta
            user.locked_until = datetime.now(timezone.utc) + timedelta(
                minutes=settings.LOCKOUT_DURATION_MINUTES
            )
        await log_action(
            db, user, "login_failed",
            details=f"Invalid password (attempt {user.failed_login_attempts})",
            ip_address=ip,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # ── Success — update login tracking
    user.last_login_at = datetime.now(timezone.utc)
    user.failed_login_attempts = 0
    user.locked_until = None

    # Generate tokens
    access_token = create_access_token(str(user.id), user.username, user.role)
    refresh_token = create_refresh_token(str(user.id))

    # Set refresh token as HttpOnly cookie
    # secure=True when not in DEBUG mode (production with HTTPS)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/auth",
    )

    await log_action(db, user, "login", ip_address=ip)

    return TokenResponse(
        access_token=access_token,
        force_password_change=user.force_password_change,
        user=UserResponse(
            id=str(user.id),
            username=user.username,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            force_password_change=user.force_password_change,
            last_login_at=user.last_login_at,
            failed_login_attempts=user.failed_login_attempts,
            created_at=user.created_at,
        ),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Use the refresh cookie to get a new access token."""
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")

    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except JWTError:
        raise HTTPException(status_code=401, detail="Refresh token expired or invalid")

    # ── Safe UUID parsing (CRITICAL fix)
    try:
        user_uuid = uuid.UUID(user_id_str)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Invalid token payload")

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()

    # ── Block refresh for inactive/locked users (HIGH fix)
    if not user or not user.is_active:
        # Clear the bad cookie
        response.delete_cookie(key="refresh_token", path="/api/auth")
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # Issue new tokens
    access_token = create_access_token(str(user.id), user.username, user.role)
    new_refresh = create_refresh_token(str(user.id))

    response.set_cookie(
        key="refresh_token",
        value=new_refresh,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/auth",
    )

    return TokenResponse(
        access_token=access_token,
        force_password_change=user.force_password_change,
        user=UserResponse(
            id=str(user.id),
            username=user.username,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            force_password_change=user.force_password_change,
            last_login_at=user.last_login_at,
            failed_login_attempts=user.failed_login_attempts,
            created_at=user.created_at,
        ),
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Clear the refresh cookie."""
    response.delete_cookie(key="refresh_token", path="/api/auth")
    await log_action(db, user, "logout", ip_address=get_client_ip(request))
    return {"detail": "Logged out successfully"}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Change own password. Used for both voluntary changes and forced first-login changes."""
    # Verify current password
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # Validate new password strength
    error = validate_password_strength(body.new_password)
    if error:
        raise HTTPException(status_code=400, detail=error)

    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="New password must be different from current password")

    # Update password
    user.hashed_password = hash_password(body.new_password)
    user.force_password_change = False

    await log_action(
        db, user, "change_password",
        target_type="user", target_id=str(user.id),
        ip_address=get_client_ip(request),
    )

    # ── Invalidate current session by clearing refresh cookie (HIGH fix)
    # Forces re-login with the new password
    response.delete_cookie(key="refresh_token", path="/api/auth")

    return {"detail": "Password changed successfully. Please log in again."}


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Return the current authenticated user's profile."""
    return UserResponse(
        id=str(user.id),
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        force_password_change=user.force_password_change,
        last_login_at=user.last_login_at,
        failed_login_attempts=user.failed_login_attempts,
        created_at=user.created_at,
    )
