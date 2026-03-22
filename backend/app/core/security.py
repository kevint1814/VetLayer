"""
Security utilities — password hashing, JWT creation/validation, auth dependencies.
"""

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User

# ── Password hashing ────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def validate_password_strength(password: str) -> Optional[str]:
    """Return an error message if password is weak, else None.
    Enforces NIST-aligned minimum requirements."""
    if len(password) < 8:
        return "Password must be at least 8 characters long."
    if not any(c.isupper() for c in password):
        return "Password must contain at least one uppercase letter."
    if not any(c.islower() for c in password):
        return "Password must contain at least one lowercase letter."
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one number."
    if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?/" for c in password):
        return "Password must contain at least one special character."
    return None


# ── JWT tokens ──────────────────────────────────────────────────────
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def create_access_token(user_id: str, username: str, role: str, company_id: str = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "company_id": company_id,
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises JWTError on failure."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


# ── Auth dependencies ───────────────────────────────────────────────

async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Extract and validate the current user from the Authorization header.
    Raises 401 if token is missing, invalid, or user not found.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Safe UUID parsing
    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Invalid token payload")

    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(User).where(User.id == user_uuid).options(selectinload(User.company))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account has been deactivated")
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """Require the current user to be an admin (super_admin, company_admin, or legacy admin)."""
    if user.role not in ("admin", "super_admin", "company_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user


async def get_current_admin_or_company_admin(user: User = Depends(get_current_user)) -> User:
    """Require super_admin or company_admin role."""
    if user.role not in ("super_admin", "company_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user


async def get_current_super_admin(user: User = Depends(get_current_user)) -> User:
    """Require super_admin role."""
    if user.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required",
        )
    return user


def require_company(user: User) -> uuid.UUID:
    """Extract company_id from user, raising 400 if user has no company (super_admin).
    Use this for WRITE operations where a company context is strictly required."""
    if not user.company_id:
        raise HTTPException(
            status_code=400,
            detail="This action requires a company context",
        )
    return user.company_id


def get_user_company_id(user: User) -> Optional[uuid.UUID]:
    """Get user's company_id, or None for super_admin (meaning 'all companies').
    Use this for READ operations where super_admin should see everything."""
    return user.company_id


def get_client_ip(request: Request) -> str:
    """Extract client IP from request, sanitizing proxy headers."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Take first IP only and sanitize (prevent header injection)
        ip = forwarded.split(",")[0].strip()
        # Basic validation — only allow reasonable IP-like strings
        if re.match(r"^[\d.:a-fA-F]+$", ip) and len(ip) <= 45:
            return ip
    return request.client.host if request.client else "unknown"
