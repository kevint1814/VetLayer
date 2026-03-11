"""
Job description CRUD endpoints.
"""

import uuid
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.models.job import Job
from app.schemas.job import JobResponse, JobCreate, JobUpdate, JobList
from app.schemas.bulk import BulkDeleteRequest, BulkDeleteResponse
from app.services.job_parser import parse_job_requirements
from app.core.security import get_current_user
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


class SmartJobCreate(BaseModel):
    """Create a job by pasting raw requirements text — VetLayer extracts skills with AI."""
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    remote_policy: Optional[str] = None
    description: str = ""
    raw_requirements: str = ""  # Raw pasted text from career page


@router.get("/", response_model=JobList)
async def list_jobs(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all job descriptions."""
    query = select(Job).offset(skip).limit(limit).order_by(Job.created_at.desc())
    if search:
        query = query.where(Job.title.ilike(f"%{search}%"))
    result = await db.execute(query)
    jobs = result.scalars().all()

    # Count query
    count_query = select(func.count()).select_from(Job)
    if search:
        count_query = count_query.where(Job.title.ilike(f"%{search}%"))
    count_result = await db.execute(count_query)
    total = count_result.scalar()

    return {"jobs": jobs, "total": total}


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Get a single job by ID."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/", response_model=JobResponse, status_code=201)
async def create_job(data: JobCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Create a new job description with manually specified skills."""
    job = Job(**data.model_dump())
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return job


@router.post("/smart", response_model=JobResponse, status_code=201)
async def create_job_smart(data: SmartJobCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Smart job creation — paste raw requirements text from a career page
    and VetLayer uses AI to extract structured skill requirements.
    """
    # If raw requirements text is provided, parse it with LLM
    required_skills = []
    preferred_skills = []
    experience_range = None

    if data.raw_requirements.strip():
        logger.info(f"Smart parsing job requirements ({len(data.raw_requirements)} chars)")
        try:
            parsed = await parse_job_requirements(data.raw_requirements, job_title=data.title)
            required_skills = parsed.get("required_skills", [])
            preferred_skills = parsed.get("preferred_skills", [])
            experience_range = parsed.get("experience_range")
        except Exception as e:
            logger.error(f"Smart parsing failed: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to parse requirements: {str(e)}"
            )

    # Use description or raw_requirements as the job description
    description = data.description.strip() or data.raw_requirements.strip() or "No description provided."

    job = Job(
        title=data.title,
        company=data.company,
        location=data.location,
        remote_policy=data.remote_policy,
        description=description,
        required_skills=required_skills if required_skills else None,
        preferred_skills=preferred_skills if preferred_skills else None,
        experience_range=experience_range,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    logger.info(
        f"Created job '{job.title}' with {len(required_skills)} required + "
        f"{len(preferred_skills)} preferred skills (smart-parsed)"
    )
    return job


@router.put("/{job_id}", response_model=JobResponse)
async def update_job(job_id: uuid.UUID, data: JobUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Update a job description. Only provided fields are changed."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    update_data = data.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(job, field_name, value)

    await db.flush()
    await db.refresh(job)
    logger.info(f"Updated job '{job.title}' (fields: {list(update_data.keys())})")
    return job


class SmartReParseRequest(BaseModel):
    """Re-parse raw requirements text for an existing job."""
    raw_requirements: str


@router.post("/{job_id}/reparse", response_model=JobResponse)
async def reparse_job_skills(job_id: uuid.UUID, data: SmartReParseRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Re-parse skills from raw requirements text for an existing job.
    Replaces the current required_skills and preferred_skills.
    """
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not data.raw_requirements.strip():
        raise HTTPException(status_code=400, detail="Raw requirements text is empty")

    try:
        parsed = await parse_job_requirements(data.raw_requirements, job_title=job.title)
        job.required_skills = parsed.get("required_skills", []) or None
        job.preferred_skills = parsed.get("preferred_skills", []) or None
        job.experience_range = parsed.get("experience_range") or job.experience_range
        job.description = data.raw_requirements.strip()

        await db.flush()
        await db.refresh(job)

        logger.info(
            f"Re-parsed job '{job.title}' — "
            f"{len(parsed.get('required_skills', []))} required + "
            f"{len(parsed.get('preferred_skills', []))} preferred skills"
        )
        return job
    except Exception as e:
        logger.error(f"Re-parse failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to re-parse requirements: {str(e)}")


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Delete a job description."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await db.delete(job)


@router.post("/delete", response_model=BulkDeleteResponse)
async def bulk_delete_jobs(request: BulkDeleteRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Bulk delete jobs by ID list. Cascade deletes associated analyses."""
    deleted = 0
    failed_ids = []
    errors = {}

    for jid in request.ids:
        try:
            result = await db.execute(select(Job).where(Job.id == jid))
            job = result.scalar_one_or_none()
            if job:
                await db.delete(job)
                deleted += 1
            else:
                failed_ids.append(str(jid))
                errors[str(jid)] = "Not found"
        except Exception as e:
            failed_ids.append(str(jid))
            errors[str(jid)] = str(e)
            logger.error(f"Failed to delete job {jid}: {e}")

    await db.flush()
    logger.info(f"Bulk deleted {deleted} jobs, {len(failed_ids)} failed")
    return BulkDeleteResponse(deleted_count=deleted, failed_ids=failed_ids, errors=errors)
