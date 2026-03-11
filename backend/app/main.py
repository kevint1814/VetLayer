"""
VetLayer – Recruiter Decision Intelligence System
Main FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.core.config import settings
from app.core.database import engine, Base, AsyncSessionLocal
from app.core.security import hash_password
from app.api.routes import health, candidates, jobs, analysis, auth, admin
from app.models.user import User

# Configure logging so errors show in terminal
logging.basicConfig(level=logging.INFO, format="%(levelname)s:  %(name)s - %(message)s")
logger = logging.getLogger(__name__)


async def seed_admin():
    """Create the default admin account if no users exist."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).limit(1))
        if result.scalar_one_or_none() is None:
            admin_user = User(
                username=settings.ADMIN_USERNAME,
                full_name=settings.ADMIN_FULL_NAME,
                hashed_password=hash_password(settings.ADMIN_PASSWORD),
                role="admin",
                is_active=True,
                force_password_change=True,
            )
            session.add(admin_user)
            await session.commit()
            logger.info(f"Seeded admin account: {settings.ADMIN_USERNAME}")
        else:
            logger.info("Users exist, skipping admin seed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    # startup
    print(f"🚀 {settings.PROJECT_NAME} v{settings.VERSION} starting up")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Ensure new columns exist on existing tables (create_all won't ALTER)
        await conn.execute(
            __import__("sqlalchemy").text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ"
            )
        )
        await conn.execute(
            __import__("sqlalchemy").text(
                "ALTER TABLE candidates ADD COLUMN IF NOT EXISTS intelligence_profile JSONB"
            )
        )
        await conn.execute(
            __import__("sqlalchemy").text(
                "ALTER TABLE candidates ADD COLUMN IF NOT EXISTS processing_status VARCHAR(20) DEFAULT 'ready'"
            )
        )
    print(f"✅ Database tables ready")

    # Seed admin account
    await seed_admin()

    yield
    # shutdown
    print(f"👋 {settings.PROJECT_NAME} shutting down")
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Recruiter Decision Intelligence System: Skill, Evidence, and Depth pipeline",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# CORS — allow the React dev server during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global error handler so 500s show in terminal ─────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.method} {request.url.path}: {exc}", exc_info=True)
    # Only expose error details in debug mode; hide internals in production
    detail = str(exc) if settings.DEBUG else "Internal server error"
    return JSONResponse(status_code=500, content={"detail": detail})


# ── Route registration ──────────────────────────────────────────────
app.include_router(health.router, prefix="/api", tags=["Health"])
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(candidates.router, prefix="/api/candidates", tags=["Candidates"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["Analysis"])
