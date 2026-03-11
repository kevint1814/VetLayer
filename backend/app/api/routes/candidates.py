"""
Candidate CRUD and resume upload endpoints.
"""

import asyncio
import uuid
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db, AsyncSessionLocal
from app.core.config import settings
from app.core.security import get_current_user
from app.models.candidate import Candidate
from app.models.user import User
from app.schemas.candidate import CandidateResponse, CandidateCreate, CandidateList
from app.schemas.bulk import BulkDeleteRequest, BulkDeleteResponse
from app.services.resume_parser import resume_parser
from app.services.intelligence_profile import generate_intelligence_profile
from app.services.pdf_intelligence_brief import generate_intelligence_brief_pdf

logger = logging.getLogger(__name__)


async def _process_resume_background(candidate_id: uuid.UUID, content: bytes, filename: str):
    """
    Background task: extract text → LLM parse → mark ready → intelligence profile (silent).
    Candidate only becomes visible to the user once marked "ready".
    """
    import time as _time
    t0 = _time.perf_counter()

    try:
        # ── Step 1: Extract text from file ────────────────────────────
        raw_text = await resume_parser.extract_text(content, filename)
        if not raw_text.strip():
            raise ValueError("Could not extract text from resume")
        t1 = _time.perf_counter()
        logger.info(f"[TIMING] {filename}: text extraction {(t1-t0)*1000:.0f}ms ({len(raw_text)} chars)")

        # ── Step 2: Full LLM structure extraction ─────────────────────
        structured = await resume_parser._llm_extract_structure(raw_text)
        t2 = _time.perf_counter()
        logger.info(f"[TIMING] {filename}: LLM parse {(t2-t1)*1000:.0f}ms")

        # Sanitize email
        raw_email = structured.get("email")
        if raw_email and ("@" not in str(raw_email) or raw_email.lower() in ("email", "n/a", "null", "none")):
            raw_email = None

        # Build ParsedResume
        from app.services.resume_parser import ParsedResume
        parsed = ParsedResume(
            name=structured.get("name", "Unknown"),
            email=raw_email,
            phone=structured.get("phone"),
            location=structured.get("location"),
            summary=structured.get("summary"),
            experience=structured.get("experience", []),
            education=structured.get("education", []),
            skills_mentioned=structured.get("skills_mentioned", []),
            certifications=structured.get("certifications", []),
            projects=structured.get("projects", []),
            links=structured.get("links", []),
            raw_text=raw_text,
            years_experience=structured.get("years_experience"),
            education_level=structured.get("education_level"),
            current_role=structured.get("current_role"),
            current_company=structured.get("current_company"),
        )

        # ── Step 3: Save all parsed data + mark READY ─────────────────
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
            candidate = result.scalar_one_or_none()
            if not candidate:
                return
            candidate.name = parsed.name if parsed.name and parsed.name != "Unknown" else candidate.name
            candidate.email = parsed.email
            candidate.phone = parsed.phone
            candidate.location = parsed.location
            candidate.resume_raw_text = (raw_text or "")[:50000]
            candidate.resume_parsed = resume_parser.to_dict(parsed)
            candidate.years_experience = parsed.years_experience
            candidate.education_level = parsed.education_level
            candidate.current_role = parsed.current_role
            candidate.current_company = parsed.current_company
            candidate.processing_status = "ready"
            await db.commit()
            logger.info(f"[TIMING] {filename}: READY in {(t2-t0)*1000:.0f}ms — {parsed.name}")

        # ── Step 4: Intelligence profile (silent — no status change) ──
        try:
            intelligence = await generate_intelligence_profile(parsed)
            t3 = _time.perf_counter()
            logger.info(f"[TIMING] {filename}: intelligence profile {(t3-t2)*1000:.0f}ms")
            if intelligence:
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
                    candidate = result.scalar_one_or_none()
                    if candidate:
                        candidate.intelligence_profile = intelligence
                        await db.commit()
        except Exception as e:
            logger.error(f"Intelligence profile failed for {candidate_id}: {e}")

    except Exception as e:
        logger.error(f"Background processing failed for {candidate_id}: {e}")
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
                candidate = result.scalar_one_or_none()
                if candidate:
                    candidate.processing_status = "failed"
                    await db.commit()
        except Exception:
            pass

router = APIRouter()


@router.get("/", response_model=CandidateList)
async def list_candidates(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all candidates with optional search."""
    query = select(Candidate).offset(skip).limit(limit).order_by(Candidate.created_at.desc())
    if search:
        query = query.where(Candidate.name.ilike(f"%{search}%"))
    result = await db.execute(query)
    candidates = result.scalars().all()

    # Count query
    count_query = select(func.count()).select_from(Candidate)
    if search:
        count_query = count_query.where(Candidate.name.ilike(f"%{search}%"))
    count_result = await db.execute(count_query)
    total = count_result.scalar()

    return {"candidates": candidates, "total": total}


@router.get("/{candidate_id}", response_model=CandidateResponse)
async def get_candidate(candidate_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Get a single candidate by ID."""
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@router.post("/", response_model=CandidateResponse, status_code=201)
async def create_candidate(data: CandidateCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Create a candidate record (without resume upload)."""
    candidate = Candidate(**data.model_dump())
    db.add(candidate)
    await db.flush()
    await db.refresh(candidate)
    return candidate


@router.post("/upload", response_model=CandidateResponse, status_code=201)
async def upload_resume(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Upload a resume file → extract text + quick parse (instant) → return with real data.
    Full LLM enrichment + intelligence profile runs in background.
    """
    if not file.filename.endswith((".pdf", ".docx", ".txt")):
        raise HTTPException(status_code=400, detail="Supported formats: PDF, DOCX, TXT")

    content = await file.read()
    max_size = getattr(settings, 'MAX_UPLOAD_SIZE_MB', 10) * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {getattr(settings, 'MAX_UPLOAD_SIZE_MB', 10)}MB")
    logger.info(f"Received resume upload: {file.filename} ({len(content)} bytes)")

    # Create placeholder (hidden from list until "ready")
    fname_clean = file.filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
    candidate = Candidate(
        name=fname_clean or "Processing...",
        resume_filename=file.filename,
        processing_status="processing",
        source="upload",
    )
    db.add(candidate)
    await db.flush()
    await db.refresh(candidate)

    logger.info(f"Upload received: {file.filename} (id={candidate.id})")

    # All processing in background (text extraction + LLM parse + intelligence)
    asyncio.create_task(_process_resume_background(candidate.id, content, file.filename))

    return candidate


@router.delete("/{candidate_id}", status_code=204)
async def delete_candidate(candidate_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Delete a candidate and all associated data."""
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    await db.delete(candidate)


@router.post("/delete", response_model=BulkDeleteResponse)
async def bulk_delete_candidates(request: BulkDeleteRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Bulk delete candidates by ID list. Cascade deletes skills + analyses."""
    deleted = 0
    failed_ids = []
    errors = {}

    for cid in request.ids:
        try:
            result = await db.execute(select(Candidate).where(Candidate.id == cid))
            candidate = result.scalar_one_or_none()
            if candidate:
                await db.delete(candidate)
                deleted += 1
            else:
                failed_ids.append(str(cid))
                errors[str(cid)] = "Not found"
        except Exception as e:
            failed_ids.append(str(cid))
            errors[str(cid)] = str(e)
            logger.error(f"Failed to delete candidate {cid}: {e}")

    await db.flush()
    logger.info(f"Bulk deleted {deleted} candidates, {len(failed_ids)} failed")
    return BulkDeleteResponse(deleted_count=deleted, failed_ids=failed_ids, errors=errors)


@router.post("/bulk-upload", status_code=201)
async def bulk_upload_resumes(
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Upload multiple resumes at once.
    Text extraction + quick parse runs inline (fast — <1s per file).
    Returns candidates with real names/emails immediately.
    Full LLM enrichment + intelligence runs in background.
    """
    import time as _time
    t0 = _time.perf_counter()

    created = []
    failed = []
    background_tasks = []  # (candidate_id, content_bytes, filename)

    for file in files:
        fname = file.filename or "unknown"
        if not file.filename or not file.filename.endswith((".pdf", ".docx", ".txt")):
            failed.append({"filename": fname, "error": "Unsupported format. Use PDF, DOCX, or TXT."})
            continue
        content = await file.read()
        max_size = getattr(settings, 'MAX_UPLOAD_SIZE_MB', 10) * 1024 * 1024
        if len(content) > max_size:
            failed.append({"filename": fname, "error": f"File too large (max {getattr(settings, 'MAX_UPLOAD_SIZE_MB', 10)}MB)"})
            continue

        # Create placeholder (hidden from list until "ready")
        fname_clean = fname.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
        candidate = Candidate(
            name=fname_clean or "Processing...",
            resume_filename=fname,
            processing_status="processing",
            source="upload",
        )
        db.add(candidate)
        await db.flush()
        await db.refresh(candidate)

        created.append({
            "id": str(candidate.id),
            "name": candidate.name,
            "resume_filename": candidate.resume_filename,
        })
        background_tasks.append((candidate.id, content, fname))
        logger.info(f"Bulk upload received: {fname} (id={candidate.id})")

    # Fire ALL background tasks at once — truly parallel
    for cid, content_bytes, fname in background_tasks:
        asyncio.create_task(_process_resume_background(cid, content_bytes, fname))

    t1 = _time.perf_counter()
    logger.info(f"[TIMING] Bulk upload: {len(created)} files quick-parsed in {(t1-t0)*1000:.0f}ms")

    return {"created": created, "failed": failed, "total_created": len(created), "total_failed": len(failed)}


# ════════════════════════════════════════════════════════════════════
# EXPORT: Intelligence Brief PDF
# ════════════════════════════════════════════════════════════════════

@router.get("/{candidate_id}/export/intelligence-brief")
async def export_intelligence_brief(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export candidate intelligence brief as a premium PDF."""
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    parsed = candidate.resume_parsed or {}
    profile = candidate.intelligence_profile

    # If profile is missing newer fields (career_timeline_briefs, ideal_roles_narrative),
    # regenerate it now so the PDF has AI-written content for every section
    if profile and (not profile.get("career_timeline_briefs") or not profile.get("ideal_roles_narrative")):
        logger.info(f"Regenerating intelligence profile for {candidate.name} (missing export fields)")
        try:
            from app.services.resume_parser import ParsedResume
            # Reconstruct ParsedResume from stored dict, using field defaults for missing keys
            fields = ParsedResume.__dataclass_fields__
            init_kwargs = {}
            for k, f in fields.items():
                if k in parsed:
                    init_kwargs[k] = parsed[k]
                # Otherwise let the dataclass default handle it
            parsed_obj = ParsedResume(**init_kwargs)
            new_profile = await generate_intelligence_profile(parsed_obj)
            if new_profile:
                profile = new_profile
                candidate.intelligence_profile = new_profile
                await db.commit()
                logger.info(f"Regenerated intelligence profile for {candidate.name}")
        except Exception as e:
            logger.warning(f"Profile regeneration failed for {candidate.name}, using existing: {e}")

    candidate_dict = {
        "name": candidate.name,
        "email": candidate.email,
        "phone": candidate.phone,
        "location": candidate.location,
        "current_role": candidate.current_role,
        "current_company": candidate.current_company,
        "years_experience": candidate.years_experience,
        "education_level": candidate.education_level,
    }

    ref_code = f"VL-{candidate.created_at.strftime('%Y')}-{str(candidate.id)[:5].upper()}"

    pdf_bytes = generate_intelligence_brief_pdf(
        candidate=candidate_dict,
        parsed=parsed,
        profile=profile,
        ref_code=ref_code,
    )

    safe_name = candidate.name.replace(" ", "_").replace("/", "_")
    filename = f"Intelligence_Brief_{safe_name}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
