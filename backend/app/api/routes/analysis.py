"""
Analysis endpoints — trigger and retrieve VetLayer analysis results.
"""

import re
import uuid
import time
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.config import settings
from app.core.security import get_current_user, require_company, get_user_company_id
from app.models.user import User
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.skill import Skill, SkillEvidence
from app.models.analysis import AnalysisResult, RiskFlag, InterviewQuestion
from app.schemas.analysis import AnalysisResponse, AnalysisTriggerRequest, AnalysisTriggerResponse
from app.schemas.bulk import BulkDeleteRequest, BulkDeleteResponse, BatchAnalysisRequest, BatchAnalysisStatus
from app.services.skill_pipeline import skill_pipeline, assessment_to_dict, timings_to_dict
from app.services.job_parser import parse_job_requirements, detect_seniority, apply_seniority_boost
from app.services.batch_runner import (
    run_batch_analysis, get_batch_state, list_batch_states,
    load_saved_batches, load_saved_batch, delete_saved_batch,
)

logger = logging.getLogger(__name__)

router = APIRouter()

def _skills_look_unparsed(skills: list) -> bool:
    """Detect if required_skills contains raw text instead of properly parsed skills."""
    if not skills:
        return False
    for s in skills:
        name = s.get("skill", "")
        if len(name) > 60:
            return True
    return False


async def _ensure_parsed_skills(job, db: AsyncSession) -> tuple:
    """
    Ensure the job has properly parsed skills.
    If required_skills looks like raw text, auto-parse using LLM.
    Applies seniority-aware depth boosting based on job title.
    Saves parsed skills back to the job record so they aren't re-parsed on every analysis.
    Returns (required_skills, preferred_skills).
    """
    required = job.required_skills or []
    preferred = job.preferred_skills or []

    if required and not _skills_look_unparsed(required):
        # Even if already parsed, apply seniority boost in case the job
        # was created before seniority detection was added
        seniority = detect_seniority(job.title or "", job.description or "")
        if seniority["depth_floor"] > 3:
            required = apply_seniority_boost(required, seniority)
            logger.info(f"Applied seniority boost ({seniority['level']}) to existing skills")
        return required, preferred

    raw_text = ""
    if required and _skills_look_unparsed(required):
        raw_text = " ".join(s.get("skill", "") for s in required)
        logger.info(f"Auto-parsing raw skill entries ({len(raw_text)} chars)")
    elif not required and job.description:
        raw_text = job.description
        logger.info(f"No required_skills, parsing from job description ({len(raw_text)} chars)")

    if raw_text:
        try:
            parsed = await parse_job_requirements(raw_text, job_title=job.title or "")
            required = parsed.get("required_skills", [])
            preferred = parsed.get("preferred_skills", preferred)
            logger.info(f"Auto-parsed: {len(required)} required, {len(preferred)} preferred skills")
            job.required_skills = required
            job.preferred_skills = preferred
            await db.flush()
        except Exception as e:
            logger.error(f"Auto-parse failed: {e}")

    return required, preferred


@router.post("/run", response_model=AnalysisTriggerResponse, status_code=202)
async def trigger_analysis(
    request: AnalysisTriggerRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Trigger a full VetLayer analysis for a candidate against a job (company-scoped).
    Runs the Skill > Evidence > Depth pipeline, then scores against job requirements.
    """
    company_id = require_company(user)
    start_time = time.time()

    # Fetch candidate
    result = await db.execute(select(Candidate).where(Candidate.id == request.candidate_id))
    candidate = result.scalar_one_or_none()
    if not candidate or candidate.company_id != company_id:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # Fetch job
    result = await db.execute(select(Job).where(Job.id == request.job_id))
    job = result.scalar_one_or_none()
    if not job or job.company_id != company_id:
        raise HTTPException(status_code=404, detail="Job not found")

    if not candidate.resume_parsed:
        raise HTTPException(
            status_code=400,
            detail="Candidate resume has not been parsed yet. Re-upload the resume."
        )

    # ── Ensure job has properly parsed skills ──────────────────────────
    required_skills, preferred_skills = await _ensure_parsed_skills(job, db)

    resume_parsed = candidate.resume_parsed

    # ── Run Job-Focused Skill Assessment ──────────────────────────────
    logger.info(f"Running analysis: candidate={candidate.name}, job={job.title}")
    logger.info(f"Assessing {len(required_skills)} required + {len(preferred_skills)} preferred skills")

    try:
        assessments, pipeline_timings = await skill_pipeline.run(
            parsed_resume=resume_parsed,
            required_skills=required_skills,
            preferred_skills=preferred_skills,
        )
    except Exception as e:
        logger.error(f"Skill pipeline failed: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis pipeline failed: {str(e)}")

    # ── Apply skill adjacency boosts ──────────────────────────────────
    _apply_adjacency_boosts(assessments)

    # ── Delete existing skills for this candidate (avoid duplicates on re-analysis)
    existing_skills = await db.execute(select(Skill).where(Skill.candidate_id == candidate.id))
    for skill in existing_skills.scalars().all():
        await db.execute(delete(SkillEvidence).where(SkillEvidence.skill_id == skill.id))
    await db.execute(delete(Skill).where(Skill.candidate_id == candidate.id))
    await db.flush()

    # ── Save skills to database (only skills found on resume) ───────
    for assessment in [a for a in assessments if a.estimated_depth > 0]:
        skill = Skill(
            candidate_id=candidate.id,
            company_id=candidate.company_id,
            name=assessment.name,
            category=assessment.category,
            estimated_depth=assessment.estimated_depth,
            depth_confidence=assessment.depth_confidence,
            depth_reasoning=assessment.depth_reasoning,
            last_used_year=assessment.last_used_year,
            years_of_use=assessment.years_of_use,
            raw_mentions={"evidence_count": len(assessment.evidence)},
        )
        db.add(skill)
        await db.flush()

        for ev in assessment.evidence:
            evidence = SkillEvidence(
                skill_id=skill.id,
                evidence_type=ev.evidence_type,
                description=ev.description,
                source_text=ev.source_text,
                strength=ev.strength,
            )
            db.add(evidence)

    # ── Compute capability scores ────────────────────────────────────
    scores = _compute_scores(
        assessments, required_skills, preferred_skills, resume_parsed,
        experience_range=job.experience_range,
        job_title=job.title or "",
    )

    # Include pipeline timings in the breakdown for transparency
    scores["breakdown"]["_pipeline_timings"] = timings_to_dict(pipeline_timings)

    processing_time = int((time.time() - start_time) * 1000)

    # ── Generate risk flags ───────────────────────────────────────────
    risk_flags = _generate_risk_flags(
        assessments, resume_parsed, scores,
        experience_range=job.experience_range,
        job_title=job.title or "",
    )

    # ── Generate interview questions ──────────────────────────────────
    interview_questions = _generate_interview_questions(
        assessments, scores, required_skills, risk_flags,
        resume_parsed=resume_parsed,
        candidate_name=candidate.name or "the candidate",
        job_title=job.title or "this role",
    )

    # ── Create analysis result ───────────────────────────────────────
    analysis = AnalysisResult(
        candidate_id=candidate.id,
        job_id=job.id,
        company_id=candidate.company_id,
        overall_score=scores["overall"],
        skill_match_score=scores["skill_match"],
        experience_score=scores["experience"],
        education_score=scores["education"],
        depth_score=scores["depth"],
        skill_breakdown=scores["breakdown"],
        strengths=scores["strengths"],
        gaps=scores["gaps"],
        summary_text=_generate_summary(candidate, job, assessments, scores),
        recommendation=scores["recommendation"],
        llm_model_used=settings.OPENAI_MODEL if settings.LLM_PROVIDER == "openai" else settings.ANTHROPIC_MODEL,
        processing_time_ms=processing_time,
    )
    db.add(analysis)
    await db.flush()
    await db.refresh(analysis)

    # ── Save risk flags ───────────────────────────────────────────────
    for rf in risk_flags:
        flag = RiskFlag(
            analysis_id=analysis.id,
            flag_type=rf["flag_type"],
            severity=rf["severity"],
            title=rf["title"],
            description=rf["description"],
            evidence=rf.get("evidence", ""),
            suggestion=rf.get("suggestion", ""),
        )
        db.add(flag)

    # ── Save interview questions ──────────────────────────────────────
    for iq in interview_questions:
        question = InterviewQuestion(
            analysis_id=analysis.id,
            category=iq["category"],
            question=iq["question"],
            rationale=iq["rationale"],
            target_skill=iq.get("target_skill"),
            expected_depth=iq.get("expected_depth"),
            priority=iq.get("priority", 5),
        )
        db.add(question)

    await db.flush()

    logger.info(
        f"Analysis complete: score={scores['overall']:.2f}, "
        f"recommendation={scores['recommendation']}, "
        f"risk_flags={len(risk_flags)}, interview_qs={len(interview_questions)}, "
        f"time={processing_time}ms"
    )

    return {"analysis_id": analysis.id, "status": "completed"}


# ═══════════════════════════════════════════════════════════════════════
# Batch Analysis Endpoints (must be before /{analysis_id} catch-all)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/batch", response_model=BatchAnalysisStatus, status_code=202)
async def trigger_batch_analysis(
    request: BatchAnalysisRequest,
    user: User = Depends(get_current_user),
):
    """
    Kick off batch analysis: N candidates x M jobs.
    Returns immediately with a batch_id. Poll /batch/{batch_id} for progress.
    """
    company_id = require_company(user)

    if not request.candidate_ids or not request.job_ids:
        raise HTTPException(status_code=400, detail="Must provide at least one candidate and one job")
    if len(request.candidate_ids) * len(request.job_ids) > 200:
        raise HTTPException(status_code=400, detail="Batch size limited to 200 pairs (candidates x jobs)")

    batch_id = await run_batch_analysis(
        candidate_ids=request.candidate_ids,
        job_ids=request.job_ids,
        force_reanalyze=request.force_reanalyze,
        company_id=company_id,
    )
    state = get_batch_state(batch_id)
    return BatchAnalysisStatus(
        batch_id=state.batch_id,
        status=state.status,
        total=state.total,
        completed=state.completed,
        failed=state.failed,
        cached=state.cached,
        results=[],
        elapsed_ms=0,
    )


@router.get("/batch", response_model=list)
async def list_batches(user: User = Depends(get_current_user)):
    """
    List all batch analyses (recent first).
    Returns from DB for persistent history, merged with in-memory for live batches.
    """
    company_id = get_user_company_id(user)
    cid_str = str(company_id) if company_id else None
    saved = await load_saved_batches(company_id=cid_str)
    # Merge: in-memory processing batches that aren't finalized in DB yet
    saved_ids = {b["batch_id"] for b in saved}
    live_states = list_batch_states()
    for s in reversed(live_states):
        if s.batch_id not in saved_ids and (not cid_str or s.company_id == cid_str):
            saved.insert(0, {
                "batch_id": s.batch_id,
                "status": s.status,
                "total": s.total,
                "completed": s.completed,
                "failed": s.failed,
                "cached": s.cached,
                "elapsed_ms": s.elapsed_ms,
                "candidate_ids": s.candidate_ids,
                "job_ids": s.job_ids,
                "job_titles": s.job_titles,
                "candidate_count": len(s.candidate_ids),
                "avg_score": 0.0,
                "top_recommendation": None,
                "created_at": None,
                "completed_at": None,
                "results": [],
            })
    return saved


@router.get("/batch/{batch_id}")
async def get_batch_progress(batch_id: str, user: User = Depends(get_current_user)):
    """
    Get batch analysis progress or saved results.
    First checks in-memory (for live polling), then falls back to DB (for history).
    """
    company_id = get_user_company_id(user)
    cid_str = str(company_id) if company_id else None

    # Check in-memory first (for active / recently completed batches)
    state = get_batch_state(batch_id)
    if state and (not cid_str or state.company_id == cid_str):
        elapsed = int((time.time() - state.started_at) * 1000) if state.status == "processing" else state.elapsed_ms

        return {
            "batch_id": state.batch_id,
            "status": state.status,
            "total": state.total,
            "completed": state.completed,
            "failed": state.failed,
            "cached": state.cached,
            "results": [
                {
                    "candidate_id": r.candidate_id,
                    "candidate_name": r.candidate_name,
                    "job_id": r.job_id,
                    "job_title": r.job_title,
                    "analysis_id": r.analysis_id,
                    "overall_score": r.overall_score,
                    "recommendation": r.recommendation,
                    "processing_time_ms": r.processing_time_ms,
                    "cached": r.cached,
                    "error": r.error,
                }
                for r in sorted(state.results, key=lambda x: x.overall_score, reverse=True)
            ],
            "elapsed_ms": elapsed,
            "candidate_ids": state.candidate_ids,
            "job_ids": state.job_ids,
            "job_titles": state.job_titles,
            "candidate_count": len(state.candidate_ids),
            "avg_score": 0.0,
            "top_recommendation": None,
            "created_at": None,
            "completed_at": None,
        }

    # Fall back to DB (filtered by company)
    saved = await load_saved_batch(batch_id, company_id=cid_str)
    if not saved:
        raise HTTPException(status_code=404, detail="Batch not found")
    return saved


@router.delete("/batch/{batch_id}", status_code=204)
async def delete_batch(batch_id: str, user: User = Depends(get_current_user)):
    """Delete a saved batch from history."""
    company_id = get_user_company_id(user)
    deleted = await delete_saved_batch(batch_id, company_id=str(company_id) if company_id else None)
    if not deleted:
        raise HTTPException(status_code=404, detail="Batch not found")
    # Also remove from in-memory if present
    from app.services.batch_runner import _batch_store
    _batch_store.pop(batch_id, None)


@router.get("/batch/{batch_id}/export/brief")
async def export_batch_brief(
    batch_id: str,
    job_id: str = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Export batch analysis as a premium PDF brief.
    Requires job_id query param to scope the brief to one job.
    Each job in a batch gets its own export.
    """
    from fastapi.responses import Response
    from app.services.pdf_batch_brief import generate_batch_brief_pdf

    company_id = get_user_company_id(user)
    cid_str = str(company_id) if company_id else None

    if not job_id:
        raise HTTPException(status_code=400, detail="job_id query parameter is required")

    # Load batch data (try in-memory first, then DB) — company-scoped
    state = get_batch_state(batch_id)
    if state and (not cid_str or state.company_id == cid_str):
        batch_data = {
            "batch_id": state.batch_id,
            "status": state.status,
            "total": state.total,
            "completed": state.completed,
            "elapsed_ms": state.elapsed_ms,
            "job_titles": state.job_titles,
            "candidate_ids": state.candidate_ids,
            "results": [r.to_dict() for r in state.results],
        }
    else:
        batch_data = await load_saved_batch(batch_id, company_id=cid_str)
        if not batch_data:
            raise HTTPException(status_code=404, detail="Batch not found")

    if batch_data.get("status") not in ("completed", "partial_failure"):
        raise HTTPException(status_code=400, detail="Batch analysis is still processing")

    # Filter results to only this job
    all_results = batch_data.get("results", [])
    results = [r for r in all_results if r.get("job_id") == job_id and not r.get("error")]
    if not results:
        raise HTTPException(status_code=400, detail="No results found for this job in the batch")

    # Fetch the job title
    import uuid as _uuid
    try:
        job_uuid = _uuid.UUID(job_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid job_id")

    job_result = await db.execute(select(Job).where(Job.id == job_uuid))
    job_obj = job_result.scalar_one_or_none()
    job_title = job_obj.title if job_obj else "Unknown Role"

    # Override batch_data to be scoped to this single job
    batch_data["job_titles"] = [job_title]
    batch_data["results"] = results

    # Collect unique candidate IDs and analysis IDs from filtered results
    cand_ids = list({r.get("candidate_id") for r in results if r.get("candidate_id")})
    analysis_ids = [r.get("analysis_id") for r in results if r.get("analysis_id")]

    cand_uuid_list = []
    for cid in cand_ids:
        try:
            cand_uuid_list.append(_uuid.UUID(cid))
        except (ValueError, TypeError):
            pass

    # Fetch candidate details
    candidates_map = {}
    if cand_uuid_list:
        result = await db.execute(
            select(Candidate).where(Candidate.id.in_(cand_uuid_list))
        )
        candidates_map = {str(c.id): c for c in result.scalars().all()}

    # Fetch analysis results (scoped to this job) with risk flags and interview questions
    analyses_map = {}  # keyed by candidate_id
    if analysis_ids:
        analysis_uuid_list = []
        for aid in analysis_ids:
            try:
                analysis_uuid_list.append(_uuid.UUID(aid))
            except (ValueError, TypeError):
                pass
        if analysis_uuid_list:
            result = await db.execute(
                select(AnalysisResult)
                .where(AnalysisResult.id.in_(analysis_uuid_list))
                .options(
                    selectinload(AnalysisResult.risk_flags),
                    selectinload(AnalysisResult.interview_questions),
                )
            )
            for a in result.scalars().all():
                # Key by candidate_id — since we filtered by job, each candidate appears once
                analyses_map[str(a.candidate_id)] = a

    # Fetch skills for each candidate
    from app.models.skill import Skill
    skills_map = {}
    if cand_uuid_list:
        result = await db.execute(
            select(Skill).where(Skill.candidate_id.in_(cand_uuid_list))
        )
        for s in result.scalars().all():
            cid_str = str(s.candidate_id)
            if cid_str not in skills_map:
                skills_map[cid_str] = []
            skills_map[cid_str].append({
                "name": s.name,
                "category": s.category,
                "estimated_depth": s.estimated_depth,
            })

    # Helper: scores are stored as 0-1 decimals, convert to 0-100 percentages
    def _pct(val):
        if val is None:
            return 0
        # Scores stored as 0-1 decimals; anything <= 1.5 is definitely a decimal
        # (even with float rounding, a 100% score won't exceed ~1.001)
        return round(val * 100) if val <= 1.5 else round(val)

    # Build enriched candidate data list (one entry per candidate for this job)
    candidates_data = []
    for r in results:
        cid = r.get("candidate_id", "")
        candidate = candidates_map.get(cid)
        analysis = analyses_map.get(cid)

        if not candidate:
            continue

        cand_dict = {
            "name": candidate.name or r.get("candidate_name", "Unknown"),
            "current_role": candidate.current_role or "",
            "location": candidate.location or "",
            "years_experience": candidate.years_experience,
            "analysis": {
                "overall_score": _pct(analysis.overall_score) if analysis else _pct(r.get("overall_score", 0)),
                "skill_match_score": _pct(analysis.skill_match_score) if analysis else 0,
                "experience_score": _pct(analysis.experience_score) if analysis else 0,
                "depth_score": _pct(analysis.depth_score) if analysis else 0,
                "education_score": _pct(analysis.education_score) if analysis else 0,
                "summary_text": analysis.summary_text if analysis else "",
                "recommendation": analysis.recommendation if analysis else r.get("recommendation", ""),
                "strengths": analysis.strengths if analysis else [],
                "gaps": analysis.gaps if analysis else [],
            },
            "risk_flags": [
                {
                    "severity": rf.severity,
                    "title": rf.title,
                    "description": rf.description,
                }
                for rf in (analysis.risk_flags if analysis else [])
            ],
            "interview_questions": [
                {
                    "question": iq.question,
                    "rationale": iq.rationale,
                    "category": iq.category,
                    "priority": iq.priority,
                }
                for iq in sorted(
                    (analysis.interview_questions if analysis else []),
                    key=lambda x: x.priority,
                )[:3]
            ],
            "skills": sorted(
                skills_map.get(cid, []),
                key=lambda x: x.get("estimated_depth", 0),
                reverse=True,
            ),
        }
        candidates_data.append(cand_dict)

    if not candidates_data:
        raise HTTPException(status_code=400, detail="No candidate data available for export")

    ref_code = f"BA-{datetime.now().strftime('%Y')}-{batch_id[:5].upper()}"
    pdf_bytes = generate_batch_brief_pdf(
        batch_data=batch_data,
        candidates_data=candidates_data,
        ref_code=ref_code,
    )

    # Sanitize job title for HTTP Content-Disposition header (latin-1 only).
    # Strip all non-ASCII chars (en-dashes, em-dashes, smart quotes, etc.)
    import unicodedata
    safe_title = unicodedata.normalize("NFKD", job_title)
    safe_title = safe_title.encode("ascii", "ignore").decode("ascii")
    safe_title = re.sub(r'[^\w\s-]', '', safe_title).strip().replace(" ", "_")
    filename = f"Batch_Analysis_Brief_{safe_title}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/ranked/{job_id}")
async def get_ranked_candidates(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get all candidates ranked by score for a specific job.
    Returns analyses sorted by overall_score descending.
    """
    result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.job_id == job_id)
        .options(
            selectinload(AnalysisResult.risk_flags),
        )
        .order_by(AnalysisResult.overall_score.desc())
    )
    analyses_raw = result.scalars().all()

    # Deduplicate: keep only the latest analysis per candidate
    latest_by_candidate: dict[str, AnalysisResult] = {}
    for a in analyses_raw:
        cid = str(a.candidate_id)
        if cid not in latest_by_candidate or a.created_at > latest_by_candidate[cid].created_at:
            latest_by_candidate[cid] = a
    analyses = sorted(latest_by_candidate.values(), key=lambda x: x.overall_score, reverse=True)

    # Fetch candidate names
    candidate_ids = [a.candidate_id for a in analyses]
    if candidate_ids:
        cand_result = await db.execute(
            select(Candidate).where(Candidate.id.in_(candidate_ids))
        )
        candidates_map = {str(c.id): c for c in cand_result.scalars().all()}
    else:
        candidates_map = {}

    # Fetch job title
    job_result = await db.execute(select(Job).where(Job.id == job_id))
    job = job_result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    ranked = []
    for rank, a in enumerate(analyses, 1):
        cand = candidates_map.get(str(a.candidate_id))
        ranked.append({
            "rank": rank,
            "analysis_id": str(a.id),
            "candidate_id": str(a.candidate_id),
            "candidate_name": cand.name if cand else "Unknown",
            "current_role": cand.current_role if cand else None,
            "current_company": cand.current_company if cand else None,
            "overall_score": a.overall_score,
            "skill_match_score": a.skill_match_score,
            "depth_score": a.depth_score,
            "recommendation": a.recommendation,
            "risk_flag_count": len(a.risk_flags),
            "processing_time_ms": a.processing_time_ms,
            "created_at": a.created_at.isoformat(),
        })

    return {
        "job_id": str(job_id),
        "job_title": job.title,
        "job_company": job.company,
        "total_candidates": len(ranked),
        "candidates": ranked,
    }


@router.get("/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(
    analysis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get a completed analysis with all details."""
    result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.id == analysis_id)
        .options(
            selectinload(AnalysisResult.risk_flags),
            selectinload(AnalysisResult.interview_questions),
        )
    )
    analysis = result.scalar_one_or_none()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return analysis


@router.get("/candidate/{candidate_id}")
async def get_analyses_for_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get all analyses for a specific candidate."""
    result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.candidate_id == candidate_id)
        .options(
            selectinload(AnalysisResult.risk_flags),
            selectinload(AnalysisResult.interview_questions),
        )
        .order_by(AnalysisResult.created_at.desc())
    )
    analyses = result.scalars().all()
    return {"analyses": [
        {
            "id": str(a.id),
            "candidate_id": str(a.candidate_id),
            "job_id": str(a.job_id),
            "overall_score": a.overall_score,
            "skill_match_score": a.skill_match_score,
            "experience_score": a.experience_score,
            "education_score": a.education_score,
            "depth_score": a.depth_score,
            "skill_breakdown": a.skill_breakdown,
            "strengths": a.strengths,
            "gaps": a.gaps,
            "summary_text": a.summary_text,
            "recommendation": a.recommendation,
            "is_overridden": a.is_overridden,
            "llm_model_used": a.llm_model_used,
            "processing_time_ms": a.processing_time_ms,
            "risk_flags": [{"id": str(rf.id), "flag_type": rf.flag_type, "title": rf.title, "description": rf.description, "severity": rf.severity} for rf in a.risk_flags],
            "interview_questions": [{"id": str(iq.id), "question": iq.question, "category": iq.category, "rationale": iq.rationale, "target_skill": iq.target_skill, "priority": iq.priority} for iq in a.interview_questions],
            "created_at": a.created_at.isoformat(),
        }
        for a in analyses
    ]}


@router.delete("/{analysis_id}", status_code=204)
async def delete_analysis(
    analysis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a single analysis and all associated risk flags + interview questions (cascade)."""
    result = await db.execute(select(AnalysisResult).where(AnalysisResult.id == analysis_id))
    analysis = result.scalar_one_or_none()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    await db.delete(analysis)
    logger.info(f"Deleted analysis {analysis_id}")


@router.post("/delete", response_model=BulkDeleteResponse)
async def bulk_delete_analyses(
    request: BulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Bulk delete analyses by ID list. Cascade deletes risk flags + interview questions."""
    deleted = 0
    failed_ids = []
    errors = {}

    for aid in request.ids:
        try:
            result = await db.execute(select(AnalysisResult).where(AnalysisResult.id == aid))
            analysis = result.scalar_one_or_none()
            if analysis:
                await db.delete(analysis)
                deleted += 1
            else:
                failed_ids.append(str(aid))
                errors[str(aid)] = "Not found"
        except Exception as e:
            failed_ids.append(str(aid))
            errors[str(aid)] = str(e)
            logger.error(f"Failed to delete analysis {aid}: {e}")

    await db.flush()
    logger.info(f"Bulk deleted {deleted} analyses, {len(failed_ids)} failed")
    return BulkDeleteResponse(deleted_count=deleted, failed_ids=failed_ids, errors=errors)


# ═══════════════════════════════════════════════════════════════════════
# Skill normalization and adjacency
# ═══════════════════════════════════════════════════════════════════════

# Canonical skill groups — all variants map to the same canonical name
_SKILL_GROUPS = [
    # Web fundamentals
    (["html", "html5", "html 5"], "html"),
    (["css", "css3", "css 3"], "css"),
    (["sass", "scss", "less"], "sass/scss"),
    (["javascript", "js", "ecmascript", "es6", "es2015", "object oriented javascript"], "javascript"),
    (["typescript", "ts"], "typescript"),
    # Frontend frameworks
    (["react", "react.js", "reactjs", "react js"], "react"),
    (["vue", "vue.js", "vuejs", "vue js"], "vue"),
    (["angular", "angular.js", "angularjs"], "angular"),
    (["next", "next.js", "nextjs"], "next.js"),
    (["svelte", "sveltekit"], "svelte"),
    # Backend
    (["node", "node.js", "nodejs", "node js"], "node.js"),
    (["python", "py", "python3"], "python"),
    (["java", "jdk"], "java"),
    (["golang", "go lang"], "go"),
    (["fastapi", "fast api", "fast-api"], "fastapi"),
    (["django", "django rest framework", "drf"], "django"),
    (["flask"], "flask"),
    (["express", "express.js", "expressjs"], "express"),
    # Databases
    (["postgresql", "postgres", "pg", "psql"], "postgresql"),
    (["mongodb", "mongo"], "mongodb"),
    (["mysql", "mariadb"], "mysql"),
    (["redis", "redis cache"], "redis"),
    (["sql", "structured query language"], "sql"),
    # Messaging / Streaming
    (["kafka", "apache kafka"], "kafka"),
    (["rabbitmq", "rabbit mq"], "rabbitmq"),
    # DevOps / Cloud
    (["aws", "amazon web services", "amazon aws"], "aws"),
    (["gcp", "google cloud", "google cloud platform"], "gcp"),
    (["azure", "microsoft azure"], "azure"),
    (["kubernetes", "k8s", "kube"], "kubernetes"),
    (["docker", "containerization"], "docker"),
    (["ci/cd", "ci cd", "cicd", "continuous integration", "continuous deployment", "github actions"], "ci/cd"),
    (["terraform", "infrastructure as code", "iac"], "terraform"),
    # Tools
    (["webpack", "module bundlers", "module bundler", "vite", "rollup", "esbuild"], "webpack"),
    (["git", "github", "gitlab", "version control"], "git"),
    # Concepts
    (["oop", "object oriented programming", "object-oriented"], "oop"),
    (["rest api", "restful", "restful api", "rest apis"], "rest api"),
    (["graphql", "graph ql"], "graphql"),
    (["microservices", "micro services", "service oriented architecture", "soa"], "microservices"),
    (["agile", "scrum", "kanban", "agile/scrum"], "agile"),
    # Browser APIs — expanded to catch all common web platform features
    (["browser apis", "web apis", "browser api", "web storage",
      "local storage", "localstorage", "session storage", "sessionstorage",
      "indexeddb", "indexed db", "service worker", "service workers",
      "web workers", "fetch api", "xmlhttprequest", "xhr",
      "dom manipulation", "dom api", "dom apis", "web components",
      "shadow dom", "custom elements", "intersection observer",
      "mutation observer", "resize observer", "performance api",
      "geolocation api", "notification api", "websocket", "websockets",
      "canvas api", "webgl", "web audio", "media api",
      "clipboard api", "drag and drop", "file api",
      "history api", "url api", "broadcast channel",
      "cache api", "caching", "browser caching",
      "web storage api", "web platform"], "browser apis"),
]

# Build lookup: variant → canonical
_SKILL_ALIASES = {}
for variants, canonical in _SKILL_GROUPS:
    for v in variants:
        _SKILL_ALIASES[v] = canonical
    _SKILL_ALIASES[canonical] = canonical


# ── Skill Adjacency Graph ─────────────────────────────────────────────
# Maps canonical skill → list of (related_skill, implied_min_depth) pairs.
# When a candidate has skill A at depth X, related skills get a floor of implied_min_depth.
# This prevents false negatives for foundational skills that frameworks inherently require.
_SKILL_ADJACENCY = {
    "react": [("html", 3), ("css", 3), ("javascript", 3), ("browser apis", 2)],
    "next.js": [("react", 3), ("html", 3), ("css", 3), ("javascript", 3), ("node.js", 2)],
    "vue": [("html", 3), ("css", 3), ("javascript", 3), ("browser apis", 2)],
    "angular": [("html", 3), ("css", 3), ("javascript", 3), ("typescript", 3), ("browser apis", 2)],
    "svelte": [("html", 3), ("css", 3), ("javascript", 3)],
    "node.js": [("javascript", 3)],
    "express": [("node.js", 3), ("javascript", 3)],
    "fastapi": [("python", 3)],
    "django": [("python", 3)],
    "flask": [("python", 3)],
    "typescript": [("javascript", 3)],
    "sass/scss": [("css", 3)],
    "kubernetes": [("docker", 2)],
    "graphql": [("rest api", 2)],
    "microservices": [("rest api", 3), ("docker", 2)],
}


def _normalize_skill(name: str) -> str:
    """Normalize a skill name for matching."""
    n = name.lower().strip().rstrip(".")
    return _SKILL_ALIASES.get(n, n)


def _depth_label(depth: int) -> str:
    """Convert depth number to a human-readable label for recruiter display."""
    labels = {
        0: "Not Found",
        1: "Awareness",
        2: "Beginner",
        3: "Intermediate/Professional",
        4: "Advanced",
        5: "Expert",
    }
    return labels.get(depth, f"Level {depth}")


def _apply_adjacency_boosts(assessments):
    """
    Apply the skill adjacency graph to boost implied skills.
    If a candidate has React at depth 4, their HTML/CSS/JS depths get floored at 3.
    This is applied post-LLM to catch cases the LLM missed.
    """
    skill_map = {}
    for a in assessments:
        skill_map[_normalize_skill(a.name)] = a

    # Collect all boost targets
    boosts = {}  # canonical_name → max implied depth
    for a in assessments:
        if a.estimated_depth >= 3:
            canonical = _normalize_skill(a.name)
            adjacencies = _SKILL_ADJACENCY.get(canonical, [])
            for related_skill, implied_depth in adjacencies:
                current_boost = boosts.get(related_skill, 0)
                boosts[related_skill] = max(current_boost, implied_depth)

    # Apply boosts
    for a in assessments:
        canonical = _normalize_skill(a.name)
        if canonical in boosts:
            implied_depth = boosts[canonical]
            if a.estimated_depth < implied_depth:
                old_depth = a.estimated_depth
                a.estimated_depth = implied_depth
                a.depth_confidence = max(a.depth_confidence, 0.7)
                a.depth_reasoning = (
                    f"Implied by adjacent skills (boosted from {old_depth} to {implied_depth}). "
                    + a.depth_reasoning
                )
                logger.info(f"Adjacency boost: {a.name} {old_depth} -> {implied_depth}")


# ═══════════════════════════════════════════════════════════════════════
# Impact marker extraction
# ═══════════════════════════════════════════════════════════════════════

_IMPACT_PATTERNS = [
    # Percentages: "reduced by 73%", "improved 40%", "95% coverage"
    (re.compile(r'(\d+)\s*%', re.IGNORECASE), "percentage"),
    # Dollar amounts: "$2M", "$500K", "$1.5 million"
    (re.compile(r'\$[\d,.]+\s*[MBKmillion|billion|thousand]*', re.IGNORECASE), "financial"),
    # User/scale numbers: "2M+ users", "500K events", "100K concurrent"
    (re.compile(r'(\d+)[MBK+]+\s*(users|daily|events|requests|concurrent|transactions)', re.IGNORECASE), "scale"),
    # Team size: "team of 4", "led 10 engineers", "mentored 5"
    (re.compile(r'(team\s+of\s+\d+|led\s+\d+|mentored?\s+\d+|managed?\s+\d+)', re.IGNORECASE), "team_leadership"),
    # Time savings: "reduced ... by", "saving X hours", "cut ... time"
    (re.compile(r'(reduced|decreased|cut|saving|saved)\s+.{0,30}\s+(by\s+\d+|time|hours|days)', re.IGNORECASE), "efficiency"),
]


def _extract_impact_markers(parsed_resume: dict) -> list:
    """
    Extract quantified impact markers from resume experience descriptions.
    Returns list of {type, text, source} dicts.
    """
    markers = []
    for exp in parsed_resume.get("experience", []):
        desc = exp.get("description", "")
        company = exp.get("company", "")
        for pattern, marker_type in _IMPACT_PATTERNS:
            for match in pattern.finditer(desc):
                # Get the sentence containing the match
                start = max(0, desc.rfind(".", 0, match.start()) + 1)
                end = desc.find(".", match.end())
                if end == -1:
                    end = min(len(desc), match.end() + 80)
                snippet = desc[start:end].strip()
                markers.append({
                    "type": marker_type,
                    "text": snippet,
                    "source": company,
                    "raw_match": match.group(),
                })
    # Deduplicate by snippet text
    seen = set()
    unique = []
    for m in markers:
        key = m["text"][:60]
        if key not in seen:
            seen.add(key)
            unique.append(m)
    return unique


# ═══════════════════════════════════════════════════════════════════════
# Recency weighting
# ═══════════════════════════════════════════════════════════════════════

def _compute_recency_factor(assessment, parsed_resume: dict) -> float:
    """
    Compute a recency factor (0.5 to 1.0) for a skill based on when it was last used.
    More recent usage = higher factor. Skills from >5 years ago get penalized.

    Uses:
    1. assessment.last_used_year if available (from LLM)
    2. Falls back to scanning experience dates for the skill
    """
    current_year = datetime.now().year

    # Try assessment's last_used_year first
    if assessment.last_used_year and assessment.last_used_year > 2000:
        years_ago = current_year - assessment.last_used_year
    else:
        # Scan experience for most recent mention of this skill
        years_ago = _estimate_years_since_last_use(assessment.name, parsed_resume, current_year)

    # Recency decay curve:
    #   0 years ago → 1.0 (current)
    #   1-2 years ago → 0.95
    #   3-4 years ago → 0.85
    #   5-7 years ago → 0.70
    #   8+ years ago → 0.50
    if years_ago <= 2:
        return 1.0
    elif years_ago <= 4:
        return 0.90
    elif years_ago <= 7:
        return 0.75
    else:
        return 0.55


def _estimate_years_since_last_use(skill_name: str, parsed_resume: dict, current_year: int) -> int:
    """Estimate how many years ago a skill was last used by scanning experience entries."""
    skill_lower = skill_name.lower()
    most_recent_year = 0

    for exp in parsed_resume.get("experience", []):
        # Check if skill is in technologies list or description
        techs = [t.lower() for t in exp.get("technologies", [])]
        desc_lower = exp.get("description", "").lower()

        if skill_lower in techs or skill_lower in desc_lower:
            end_date = exp.get("end_date", "")
            if isinstance(end_date, str):
                if "present" in end_date.lower() or "current" in end_date.lower():
                    return 0  # Currently using
                # Try to parse year from date string
                year_match = re.search(r'20\d{2}', end_date)
                if year_match:
                    year = int(year_match.group())
                    most_recent_year = max(most_recent_year, year)

    if most_recent_year > 0:
        return current_year - most_recent_year
    return 3  # Default assumption: moderately recent


# ═══════════════════════════════════════════════════════════════════════
# Education scoring (uses actual parsed education data)
# ═══════════════════════════════════════════════════════════════════════

_EDUCATION_LEVELS = {
    "phd": 1.0,
    "ph.d": 1.0,
    "doctorate": 1.0,
    "master": 0.85,
    "master's": 0.85,
    "msc": 0.85,
    "ms": 0.85,
    "mba": 0.85,
    "bachelor": 0.65,
    "bachelor's": 0.65,
    "bsc": 0.65,
    "bs": 0.65,
    "ba": 0.65,
    "associate": 0.45,
    "associate's": 0.45,
    "bootcamp": 0.35,
    "certificate": 0.30,
    "self-taught": 0.25,
    "high school": 0.15,
}


def _compute_education_score(assessments, required_skills: list, parsed_resume: dict) -> float:
    """
    Compute education score from actual parsed education data plus skill signals.

    Components:
    - Degree level match (0.0-0.40): higher degree = higher score
    - Field relevance (0.0-0.25): CS/Engineering/Math fields score higher for tech roles
    - Certifications bonus (0.0-0.15): relevant certs add value
    - Skill breadth proxy (0.0-0.20): % of required skills found
    """
    education = parsed_resume.get("education", [])
    education_level = parsed_resume.get("education_level", "")
    certifications = parsed_resume.get("certifications", [])

    # Component 1: Degree level (0-0.40)
    degree_score = 0.0
    if education_level:
        level_lower = education_level.lower().strip()
        for key, score in _EDUCATION_LEVELS.items():
            if key in level_lower:
                degree_score = score
                break
    # Also check individual education entries
    for edu in education:
        degree = edu.get("degree", "").lower()
        for key, score in _EDUCATION_LEVELS.items():
            if key in degree:
                degree_score = max(degree_score, score)
                break
    degree_component = degree_score * 0.40

    # Component 2: Field relevance (0-0.25)
    relevant_fields = {"computer science", "software engineering", "computer engineering",
                       "information technology", "data science", "mathematics",
                       "electrical engineering", "information systems",
                       "artificial intelligence", "machine learning",
                       "distributed systems", "cybersecurity"}
    field_score = 0.0
    for edu in education:
        field = edu.get("field", "").lower()
        for rf in relevant_fields:
            if rf in field:
                field_score = 1.0
                break
        if field_score > 0:
            break
    field_component = field_score * 0.25

    # Component 3: Certifications bonus (0-0.15)
    cert_score = min(len(certifications) / 3.0, 1.0) if certifications else 0.0
    cert_component = cert_score * 0.15

    # Component 4: Skill breadth (0-0.20)
    if assessments and required_skills:
        found = sum(1 for a in assessments if a.estimated_depth > 0)
        total = len(required_skills) or 1
        breadth = min(found / total, 1.0)
    else:
        breadth = 0.3
    breadth_component = breadth * 0.20

    return round(degree_component + field_component + cert_component + breadth_component, 3)


# ═══════════════════════════════════════════════════════════════════════
# Risk flag generation
# ═══════════════════════════════════════════════════════════════════════

def _generate_risk_flags(assessments, parsed_resume: dict, scores: dict,
                         experience_range: dict = None, job_title: str = "") -> list:
    """
    Deterministic risk flag generation based on analysis data.
    No LLM calls needed — purely rule-based.

    Two categories of flags:
      A. Inconsistency/red-flag detectors (inflation, gaps, staleness, seniority mismatch)
      B. Weak-profile warnings (low overall score, thin resume, massive skill gaps,
         shallow depth across the board, no transferable strengths)
    """
    if experience_range is None:
        experience_range = {}
    flags = []

    overall = scores.get("overall", 0)
    recommendation = scores.get("recommendation", "")
    strengths = scores.get("strengths", [])
    gaps_list = scores.get("gaps", [])
    breakdown = scores.get("breakdown", {})

    # ═══════════════════════════════════════════════════════════════════
    # A. Inconsistency / Red-flag detectors
    # ═══════════════════════════════════════════════════════════════════

    # A1. Seniority mismatch detection
    seniority_info = detect_seniority(job_title, "")
    seniority_level = seniority_info.get("level", "mid")
    candidate_years = _estimate_candidate_years(parsed_resume)

    if seniority_level in ("principal", "staff") and candidate_years is not None and candidate_years < 8:
        flags.append({
            "flag_type": "seniority_mismatch",
            "severity": "high",
            "title": _sanitize_text(f"Experience level may not match {seniority_level} role requirements"),
            "description": _sanitize_text(
                f"This is a {seniority_level}-level position, typically requiring 8+ years of experience. "
                f"The candidate appears to have approximately {candidate_years} years. "
                f"Verify whether the candidate's impact and scope compensate for fewer years."
            ),
            "evidence": f"Estimated {candidate_years} years experience vs. {seniority_level}-level role",
            "suggestion": _sanitize_text(
                "Ask about scope of past work: system design ownership, mentoring, "
                "cross-team impact, and technical strategy contributions."
            ),
        })
    elif seniority_level in ("senior", "lead", "architect") and candidate_years is not None and candidate_years < 4:
        flags.append({
            "flag_type": "seniority_mismatch",
            "severity": "medium",
            "title": _sanitize_text(f"Experience level may be light for a {seniority_level} role"),
            "description": _sanitize_text(
                f"This is a {seniority_level}-level position, usually requiring 4+ years of experience. "
                f"The candidate appears to have approximately {candidate_years} years. "
                f"Strong technical depth or demonstrated leadership could compensate."
            ),
            "evidence": f"Estimated {candidate_years} years experience vs. {seniority_level}-level role",
            "suggestion": _sanitize_text(
                "Focus interview questions on leadership experience, mentoring, "
                "and independent technical decision-making."
            ),
        })

    # A2. Experience range mismatch (from job's explicit requirements)
    min_years = experience_range.get("min_years") if experience_range else None
    if min_years and candidate_years is not None and candidate_years < min_years:
        shortfall = min_years - candidate_years
        flags.append({
            "flag_type": "experience_shortfall",
            "severity": "high" if shortfall >= 3 else "medium",
            "title": f"Candidate is approximately {shortfall} year{'s' if shortfall != 1 else ''} short of experience requirement",
            "description": _sanitize_text(
                f"The job requires a minimum of {min_years} years of experience, "
                f"but the candidate appears to have approximately {candidate_years} years."
            ),
            "evidence": f"Required: {min_years}+ years, Estimated: {candidate_years} years",
            "suggestion": "Assess whether exceptional skill depth or relevant project complexity could offset fewer years.",
        })

    # A3. Skill inflation detection: high claimed depth but low evidence
    for a in assessments:
        if a.estimated_depth >= 4 and a.depth_confidence < 0.5 and len(a.evidence) < 2:
            flags.append({
                "flag_type": "skill_inflation",
                "severity": "medium",
                "title": _sanitize_text(f"Low evidence for claimed {a.name} expertise"),
                "description": _sanitize_text(
                    f"Candidate was assessed at depth {a.estimated_depth} for {a.name}, "
                    f"but confidence is only {a.depth_confidence:.0%} with {len(a.evidence)} evidence item{'s' if len(a.evidence) != 1 else ''}. "
                    f"Consider probing this skill further in interview."
                ),
                "evidence": _sanitize_text(a.depth_reasoning or ""),
                "suggestion": _sanitize_text(f"Ask specific architecture or implementation questions about {a.name} to verify depth."),
            })

    # A4. Employment gaps
    experiences = parsed_resume.get("experience", [])
    if len(experiences) >= 2:
        for i in range(len(experiences) - 1):
            end_date = experiences[i].get("end_date", "")
            start_date = experiences[i + 1].get("start_date", "")
            gap = _estimate_gap_months(end_date, start_date)
            if gap is not None and gap > 6:
                flags.append({
                    "flag_type": "employment_gap",
                    "severity": "low" if gap < 12 else "medium",
                    "title": _sanitize_text(f"Employment gap of approximately {gap} months"),
                    "description": _sanitize_text(
                        f"There appears to be a gap of about {gap} months "
                        f"between {experiences[i].get('company', 'previous role')} and "
                        f"{experiences[i + 1].get('company', 'next role')}."
                    ),
                    "evidence": _sanitize_text(f"End date: {end_date}, Next start: {start_date}"),
                    "suggestion": "Ask about what the candidate was doing during this period.",
                })

    # A5. Recency concerns: key skills not used recently
    current_year = datetime.now().year
    for a in assessments:
        if a.estimated_depth >= 3 and a.last_used_year and (current_year - a.last_used_year) > 5:
            years_ago = current_year - a.last_used_year
            flags.append({
                "flag_type": "stale_skill",
                "severity": "low" if years_ago <= 7 else "medium",
                "title": _sanitize_text(f"{a.name} last used {years_ago} years ago"),
                "description": _sanitize_text(
                    f"The candidate has strong {a.name} skills (depth {a.estimated_depth}), "
                    f"but it was last used around {a.last_used_year}. "
                    f"The technology landscape may have changed significantly since then."
                ),
                "evidence": _sanitize_text(a.depth_reasoning or ""),
                "suggestion": _sanitize_text(f"Ask about recent experience with modern {a.name} practices and changes."),
            })

    # ═══════════════════════════════════════════════════════════════════
    # B. Weak-profile warnings — alert the recruiter that this candidate
    #    is substantially underqualified, not just "suspicious"
    # ═══════════════════════════════════════════════════════════════════

    # B1. Overall weak candidate (score < 0.35 → "no" or "strong_no")
    if overall < 0.35:
        severity = "critical" if overall < 0.20 else "high"
        score_pct = round(overall * 100)
        flags.append({
            "flag_type": "weak_overall_profile",
            "severity": severity,
            "title": f"Substantially underqualified (score {score_pct}/100)",
            "description": _sanitize_text(
                f"This candidate scored {score_pct}/100 overall, indicating a poor match "
                f"for the role. The skill match, experience depth, and qualifications are "
                f"significantly below what is required. "
                f"Proceeding to interview may not be the best use of time."
            ),
            "evidence": _sanitize_text(
                f"Overall: {score_pct}, Skill match: {round(scores.get('skill_match', 0) * 100)}, "
                f"Depth: {round(scores.get('depth', 0) * 100)}, "
                f"Experience: {round(scores.get('experience', 0) * 100)}, "
                f"Education: {round(scores.get('education', 0) * 100)}"
            ),
            "suggestion": "Consider whether this candidate should be fast-tracked to rejection or reviewed for a different, less demanding role.",
        })

    # B2. Thin resume — very few skills detected and minimal work history
    matched_assessments = [a for a in assessments if a.estimated_depth > 0]
    total_evidence_items = sum(len(a.evidence) for a in assessments)
    experience_count = len(experiences)

    if len(matched_assessments) <= 2 and total_evidence_items < 5 and experience_count <= 1:
        flags.append({
            "flag_type": "thin_resume",
            "severity": "medium",
            "title": "Very thin resume with limited content",
            "description": _sanitize_text(
                f"The resume contains very little analyzable content: "
                f"only {len(matched_assessments)} skill{'s' if len(matched_assessments) != 1 else ''} detected, "
                f"{total_evidence_items} evidence item{'s' if total_evidence_items != 1 else ''}, "
                f"and {experience_count} work experience entr{'ies' if experience_count != 1 else 'y'}. "
                f"There was very limited information to work with, so scores may be unreliable."
            ),
            "evidence": f"{len(matched_assessments)} skills matched, {total_evidence_items} evidence items, {experience_count} experience entries",
            "suggestion": "Request a more detailed resume or conduct a preliminary phone screen to gather additional information before proceeding.",
        })

    # B3. Massive skill gap — more than half of required skills are missing entirely
    required_skills_in_breakdown = [
        s for s, info in breakdown.items()
        if not info.get("preferred", False)
    ]
    missing_skills = [
        s for s in required_skills_in_breakdown
        if breakdown[s].get("estimated_depth", 0) == 0
    ]
    total_required = len(required_skills_in_breakdown)

    if total_required >= 4 and len(missing_skills) > total_required / 2:
        missing_names = ", ".join(missing_skills[:6])
        flags.append({
            "flag_type": "massive_skill_gap",
            "severity": "high",
            "title": f"{len(missing_skills)} of {total_required} required skills completely absent",
            "description": _sanitize_text(
                f"More than half the required skills were not found anywhere on the resume. "
                f"Missing: {missing_names}{'...' if len(missing_skills) > 6 else ''}. "
                f"This level of skill gap would require significant training and ramp-up time."
            ),
            "evidence": f"Missing {len(missing_skills)} of {total_required} required skills: {missing_names}",
            "suggestion": "This candidate likely needs to be evaluated for a more junior role or a different position entirely.",
        })

    # B4. Shallow depth across the board — candidate has only surface-level
    #     knowledge (depth 1-2) on most required skills that they DO have
    matched_required = [
        (s, info) for s, info in breakdown.items()
        if not info.get("preferred", False) and info.get("estimated_depth", 0) > 0
    ]
    if len(matched_required) >= 3:
        shallow_count = sum(
            1 for _, info in matched_required
            if info.get("estimated_depth", 0) <= 2 and info.get("required_depth", 0) >= 3
        )
        if shallow_count >= len(matched_required) * 0.6 and shallow_count >= 3:
            flags.append({
                "flag_type": "shallow_depth",
                "severity": "medium",
                "title": f"Surface-level knowledge across {shallow_count} required skills",
                "description": _sanitize_text(
                    f"The candidate shows only awareness or beginner-level depth "
                    f"(depth 1-2) on {shallow_count} skills where the role requires "
                    f"intermediate level or higher (depth 3+). "
                    f"The candidate may have encountered these technologies but lacks "
                    f"the hands-on depth needed for this role."
                ),
                "evidence": f"{shallow_count} of {len(matched_required)} matched skills are at depth 1-2 vs. required depth 3+",
                "suggestion": "Conduct a technical screen to verify actual working knowledge. Surface-level mentions on a resume don't always indicate real capability.",
            })

    # B5. No transferable strengths — the strengths list is empty or
    #     contains only "preferred skill" matches (nothing on required skills)
    required_strengths = [s for s in strengths if "preferred skill" not in s.lower()]
    if len(required_strengths) == 0 and total_required >= 3:
        flags.append({
            "flag_type": "no_strengths",
            "severity": "high" if overall < 0.40 else "medium",
            "title": "No notable strengths identified for required skills",
            "description": _sanitize_text(
                f"The analysis could not identify any areas where the candidate meets or "
                f"exceeds the requirements for this role's core skills. "
                f"{'The candidate scored ' + str(round(overall * 100)) + '/100 overall. ' if overall < 0.40 else ''}"
                f"Without at least one area of strength, there is little foundation "
                f"to build a case for this candidacy."
            ),
            "evidence": f"0 strengths on required skills out of {total_required} evaluated",
            "suggestion": "Unless the candidate has relevant experience not captured on their resume, this may not be a viable match.",
        })

    # B6. Education mismatch — if the role likely needs a degree and
    #     education score is very low
    education_score = scores.get("education", 0)
    if education_score < 0.2 and overall < 0.50:
        education_data = parsed_resume.get("education", [])
        has_any_degree = any(
            edu.get("degree") or edu.get("institution")
            for edu in education_data
        ) if education_data else False
        if not has_any_degree:
            flags.append({
                "flag_type": "education_gap",
                "severity": "low",
                "title": "No formal education detected on resume",
                "description": _sanitize_text(
                    "No degree or formal education was found on the resume. "
                    "While not always required, many roles expect at least some "
                    "formal technical training or certification. "
                    "This may also indicate the resume is incomplete."
                ),
                "evidence": "No education entries found in parsed resume",
                "suggestion": "Ask about the candidate's educational background and any certifications or bootcamps not listed on the resume.",
            })

    return flags


def _estimate_gap_months(end_date_str: str, start_date_str: str) -> int | None:
    """Estimate gap in months between two date strings."""
    if not end_date_str or not start_date_str:
        return None
    if "present" in end_date_str.lower() or "current" in end_date_str.lower():
        return None

    end_year_match = re.search(r'(20\d{2})', end_date_str)
    start_year_match = re.search(r'(20\d{2})', start_date_str)
    if not end_year_match or not start_year_match:
        return None

    end_year = int(end_year_match.group(1))
    start_year = int(start_year_match.group(1))

    # Try to extract month from common date formats like "01/2020", "3-2019", "Jan 2020"
    _MONTH_NAMES = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    def _extract_month(date_str: str) -> int:
        """Extract month number from a date string. Returns 6 (mid-year) if unknown."""
        # Try "MM/YYYY" or "MM-YYYY" format
        m = re.search(r'(\d{1,2})\s*[/\-]\s*\d{4}', date_str)
        if m:
            month = int(m.group(1))
            if 1 <= month <= 12:
                return month
        # Try month name ("Jan", "January", etc.)
        lower = date_str.lower()
        for name, num in _MONTH_NAMES.items():
            if name in lower:
                return num
        return 6  # default to mid-year

    end_month = _extract_month(end_date_str)
    start_month = _extract_month(start_date_str)

    gap = (start_year - end_year) * 12 + (start_month - end_month)
    return max(0, gap)


# ═══════════════════════════════════════════════════════════════════════
# Interview question generation
# ═══════════════════════════════════════════════════════════════════════

def _generate_interview_questions(
    assessments, scores: dict, required_skills: list, risk_flags: list,
    resume_parsed: dict = None, candidate_name: str = "the candidate",
    job_title: str = "this role",
) -> list:
    """
    Generate targeted, dynamic interview questions based on analysis results.
    Each question is specific to the actual candidate data — evidence found,
    projects mentioned, specific gaps, and risk flag details.
    Deterministic, rule-based — no LLM calls.
    """
    if resume_parsed is None:
        resume_parsed = {}

    questions = []
    priority_counter = 1

    # ── Helper: extract concrete evidence from an assessment ──────────
    def _get_evidence_details(assessment) -> dict:
        """Pull specific projects, tools, and context from evidence."""
        projects = []
        source_snippets = []
        for ev in (assessment.evidence or []):
            desc = (ev.description or "").strip()
            src = (ev.source_text or "").strip()
            if desc and len(desc) > 10:
                projects.append(desc)
            if src and len(src) > 10:
                source_snippets.append(src[:150])
        return {"projects": projects[:3], "snippets": source_snippets[:3]}

    # ── Helper: get candidate's experience entries for context ────────
    experience_entries = resume_parsed.get("experience", [])
    recent_companies = []
    for exp in experience_entries[:3]:
        if isinstance(exp, dict):
            company = exp.get("company", "")
            title = exp.get("title", "")
            if company:
                recent_companies.append(f"{title} at {company}" if title else company)

    first_name = candidate_name.split()[0] if candidate_name and candidate_name != "the candidate" else ""

    # ═══════════════════════════════════════════════════════════════════
    # 1. DEPTH PROBES — high-depth skills with lower confidence
    # ═══════════════════════════════════════════════════════════════════
    for a in assessments:
        if a.estimated_depth >= 4 and a.depth_confidence < 0.75:
            ev = _get_evidence_details(a)
            reasoning = (a.depth_reasoning or "")[:200]

            # Build a question that references what we actually found
            if ev["projects"]:
                project_ref = ev["projects"][0]
                question = (
                    f"Your resume mentions {a.name} in the context of: \"{project_ref[:100]}\" — "
                    f"can you walk me through the architecture decisions you made there? "
                    f"What alternatives did you evaluate and why did you choose this approach?"
                )
            elif a.years_of_use and a.years_of_use >= 3:
                question = (
                    f"You've worked with {a.name} for about {a.years_of_use:.0f} years. "
                    f"Describe a situation where you had to make a non-obvious architectural decision with {a.name}. "
                    f"What constraints were you working with and what was the outcome?"
                )
            else:
                question = (
                    f"Your resume suggests advanced {a.name} experience. "
                    f"Tell me about a time you had to debug a particularly tricky issue or design something from scratch "
                    f"using {a.name}. What made it challenging?"
                )

            questions.append({
                "category": "depth_probe",
                "question": _sanitize_text(question),
                "rationale": _sanitize_text(
                    f"Rated depth {a.estimated_depth}/5 but only {a.depth_confidence:.0%} confidence. "
                    f"{reasoning}"
                ),
                "target_skill": a.name,
                "expected_depth": a.estimated_depth,
                "priority": priority_counter,
            })
            priority_counter += 1

    # ═══════════════════════════════════════════════════════════════════
    # 2. GAP EXPLORATION — missing or below-threshold required skills
    # ═══════════════════════════════════════════════════════════════════
    breakdown = scores.get("breakdown", {})
    for req in required_skills:
        skill_name = req.get("skill", "")
        min_depth = req.get("min_depth", 2)
        info = breakdown.get(skill_name, {})

        if not info or not info.get("matched_skill"):
            # Completely missing skill
            # Check if there's an adjacent/related skill the candidate has
            # (same category as any of the candidate's existing strong skills)
            related = []
            # Get the category of the missing skill from the required_skills list
            missing_category = req.get("category", "")
            for a in assessments:
                if a.estimated_depth >= 2 and _normalize_skill(a.name) != _normalize_skill(skill_name):
                    # If the candidate's skill shares a category with the missing skill
                    if a.category and missing_category and a.category.lower() == missing_category.lower():
                        related.append(a.name)

            if related:
                question = (
                    f"This {job_title} position requires {skill_name}, which doesn't appear on your resume. "
                    f"However, you have experience with {', '.join(related[:2])} which is in a similar space. "
                    f"Have you had any exposure to {skill_name}, and how quickly do you think you could ramp up "
                    f"given your {related[0]} background?"
                )
            else:
                depth_label = _depth_label(min_depth)
                question = (
                    f"{skill_name} is a key requirement for this {job_title} role "
                    f"(we need {depth_label}-level proficiency). "
                    f"It wasn't found on your resume — have you worked with it outside of what's listed, "
                    f"or with closely related technologies that would help you pick it up?"
                )

            questions.append({
                "category": "gap_exploration",
                "question": _sanitize_text(question),
                "rationale": _sanitize_text(
                    f"{skill_name} is required at depth {min_depth} but no evidence was found on the resume."
                ),
                "target_skill": skill_name,
                "priority": priority_counter,
            })
            priority_counter += 1

        elif not info.get("match"):
            # Below required depth
            actual_depth = info.get("estimated_depth", 0)
            shortfall = min_depth - actual_depth
            confidence = info.get("confidence", 0)
            reasoning = info.get("reasoning", "")

            # Find the assessment for richer context
            assessment_obj = next(
                (a for a in assessments if _normalize_skill(a.name) == _normalize_skill(skill_name)),
                None
            )
            ev = _get_evidence_details(assessment_obj) if assessment_obj else {"projects": [], "snippets": []}

            if ev["projects"]:
                project_ref = ev["projects"][0][:80]
                question = (
                    f"For {skill_name}, we found evidence like \"{project_ref}\" — "
                    f"but the role needs deeper expertise (depth {min_depth} vs your current {actual_depth}). "
                    f"Can you describe a project where you were the primary decision-maker for {skill_name} implementation?"
                )
            else:
                actual_label = _depth_label(actual_depth)
                needed_label = _depth_label(min_depth)
                question = (
                    f"Your {skill_name} experience appears to be at a {actual_label} level, "
                    f"but this role needs {needed_label}. "
                    f"What's the most complex thing you've built or architected with {skill_name}? "
                    f"Are there areas of it you haven't explored yet?"
                )

            questions.append({
                "category": "gap_exploration",
                "question": _sanitize_text(question),
                "rationale": _sanitize_text(
                    f"{skill_name}: depth {actual_depth} vs required {min_depth} "
                    f"(shortfall of {shortfall}). {reasoning}"
                ),
                "target_skill": skill_name,
                "priority": priority_counter,
            })
            priority_counter += 1

        if priority_counter > 5:
            break  # Don't generate too many gap questions

    # ═══════════════════════════════════════════════════════════════════
    # 3. RISK FLAG INVESTIGATION — tailored to specific flag types
    # ═══════════════════════════════════════════════════════════════════
    for rf in risk_flags[:4]:
        flag_type = rf.get("flag_type", "")
        rf_desc = rf.get("description", "")
        rf_evidence = rf.get("evidence", "")

        if flag_type == "skill_inflation":
            # Extract the specific skill and what we found
            skill = rf.get("title", "").split("claimed ")[-1].split(" expertise")[0].strip()
            assessment_obj = next(
                (a for a in assessments if _normalize_skill(a.name) == _normalize_skill(skill)),
                None
            )
            ev = _get_evidence_details(assessment_obj) if assessment_obj else {"projects": [], "snippets": []}

            if ev["projects"]:
                question = (
                    f"Your resume lists {skill} and mentions \"{ev['projects'][0][:80]}\" — "
                    f"but the evidence seems lighter than expected for the depth level claimed. "
                    f"Can you take me through exactly what you built, what tech decisions were yours, "
                    f"and what you'd do differently today?"
                )
            else:
                question = (
                    f"You've listed {skill} on your resume, but we couldn't find detailed project evidence. "
                    f"Can you describe a specific production system you built with {skill}, "
                    f"including the scale, challenges, and your exact role in the implementation?"
                )

            questions.append({
                "category": "red_flag",
                "question": _sanitize_text(question),
                "rationale": _sanitize_text(rf_desc),
                "target_skill": skill,
                "priority": priority_counter,
            })
            priority_counter += 1

        elif flag_type == "employment_gap":
            question = (
                f"There appears to be a gap in your work history. "
                f"During that period, were you doing anything relevant to your career — "
                f"freelancing, personal projects, upskilling, or contributing to open source? "
                f"Any of those can count as valid experience."
            )
            questions.append({
                "category": "behavioral",
                "question": _sanitize_text(question),
                "rationale": _sanitize_text(rf_desc),
                "priority": priority_counter,
            })
            priority_counter += 1

        elif flag_type == "stale_skill":
            # Title format: "{skill_name} last used {N} years ago"
            raw_title = rf.get("title", "")
            skill = re.sub(r'\s+last used.*$', '', raw_title).strip() if "last used" in raw_title else raw_title.strip()
            assessment_obj = next(
                (a for a in assessments if _normalize_skill(a.name) == _normalize_skill(skill)),
                None
            )
            last_year = assessment_obj.last_used_year if assessment_obj else None
            if last_year:
                question = (
                    f"Your most recent {skill} experience appears to be from around {last_year}. "
                    f"The technology has evolved significantly since then — "
                    f"are you up to date with the latest changes? "
                    f"Have you done any recent side projects or coursework with modern {skill}?"
                )
            else:
                question = (
                    f"{skill} appears on your resume but the experience may not be recent. "
                    f"How current is your knowledge? Are you familiar with the latest version "
                    f"and ecosystem changes?"
                )
            questions.append({
                "category": "red_flag",
                "question": _sanitize_text(question),
                "rationale": _sanitize_text(rf_desc),
                "target_skill": skill,
                "priority": priority_counter,
            })
            priority_counter += 1

        elif flag_type == "seniority_mismatch":
            question = (
                f"This is a {job_title} position. "
                f"Can you give me an example of a time you led a technical initiative end-to-end — "
                f"from requirements gathering through to production deployment and monitoring? "
                f"What was your team size and how did you handle technical disagreements?"
            )
            questions.append({
                "category": "behavioral",
                "question": _sanitize_text(question),
                "rationale": _sanitize_text(rf_desc),
                "priority": priority_counter,
            })
            priority_counter += 1

        elif flag_type in ("weak_overall_profile", "thin_resume", "massive_skill_gap"):
            # For weak profiles, ask about what's NOT on the resume
            question = (
                f"Looking at your background, there are some gaps relative to this {job_title} role. "
                f"Beyond what's on your resume, what other technical experience or projects "
                f"do you have that might be relevant? Sometimes candidates don't list everything — "
                f"side projects, bootcamps, or open-source contributions can all be valuable."
            )
            questions.append({
                "category": "gap_exploration",
                "question": _sanitize_text(question),
                "rationale": _sanitize_text(rf_desc),
                "priority": priority_counter,
            })
            priority_counter += 1

        elif flag_type == "shallow_depth":
            question = (
                f"Your resume shows breadth across multiple technologies, "
                f"but this role needs deeper specialization. "
                f"Which of the technologies you've listed would you say you know most deeply? "
                f"Walk me through a complex problem you solved with it — I want to understand "
                f"your depth of thinking, not just usage."
            )
            questions.append({
                "category": "depth_probe",
                "question": _sanitize_text(question),
                "rationale": _sanitize_text(rf_desc),
                "priority": priority_counter,
            })
            priority_counter += 1

    # ═══════════════════════════════════════════════════════════════════
    # 4. STRENGTH VERIFICATION — confirm top skills with specific evidence
    # ═══════════════════════════════════════════════════════════════════
    strength_skills = sorted(
        [a for a in assessments if a.estimated_depth >= 4 and a.depth_confidence >= 0.75],
        key=lambda a: (a.estimated_depth, a.depth_confidence),
        reverse=True,
    )
    for a in strength_skills[:2]:
        ev = _get_evidence_details(a)

        if ev["projects"] and len(ev["projects"]) >= 2:
            # Reference multiple projects to show we read the resume
            question = (
                f"You clearly have strong {a.name} experience — "
                f"we can see evidence across multiple projects including \"{ev['projects'][0][:60]}\" "
                f"and \"{ev['projects'][1][:60]}\". "
                f"Which of these was the most technically challenging, and what would you improve "
                f"if you were to redo it today?"
            )
        elif ev["projects"]:
            question = (
                f"Your {a.name} work on \"{ev['projects'][0][:80]}\" stood out. "
                f"What was the scale of this project and what was the hardest technical problem "
                f"you had to solve? How did your approach evolve over the project?"
            )
        elif recent_companies:
            question = (
                f"Your {a.name} expertise looks solid based on your work at {recent_companies[0]}. "
                f"Tell me about the most impactful thing you built with {a.name} there — "
                f"what business problem did it solve and how did you measure success?"
            )
        else:
            question = (
                f"Your {a.name} skills rate very well for this role. "
                f"What's a project where you pushed {a.name} to its limits or used it in an unconventional way? "
                f"I want to understand the ceiling of your experience."
            )

        questions.append({
            "category": "skill_verification",
            "question": _sanitize_text(question),
            "rationale": _sanitize_text(
                f"Strong signal: depth {a.estimated_depth}/5, {a.depth_confidence:.0%} confidence. "
                f"Verify to build the hiring case."
            ),
            "target_skill": a.name,
            "expected_depth": a.estimated_depth,
            "priority": priority_counter,
        })
        priority_counter += 1

    # ═══════════════════════════════════════════════════════════════════
    # 5. CULTURE/FIT — context-aware closing question
    # ═══════════════════════════════════════════════════════════════════
    if recent_companies and len(questions) < 10:
        question = (
            f"You've worked at {recent_companies[0]}"
            + (f" and {recent_companies[1]}" if len(recent_companies) > 1 else "")
            + f". What kind of engineering culture brings out your best work? "
            f"How does that compare to what you've experienced so far?"
        )
        questions.append({
            "category": "behavioral",
            "question": _sanitize_text(question),
            "rationale": "Cultural fit assessment based on candidate's work history.",
            "priority": priority_counter,
        })
        priority_counter += 1

    return questions[:10]  # Cap at 10 questions


# ═══════════════════════════════════════════════════════════════════════
# Scoring
# ═══════════════════════════════════════════════════════════════════════

def _compute_scores(assessments, required_skills: list, preferred_skills: list, parsed_resume: dict = None,
                     experience_range: dict = None, job_title: str = "") -> dict:
    """
    Compute capability scores from job-focused pipeline assessments.
    Includes recency weighting, impact markers, adjacency-boosted skills,
    and experience range validation.
    """
    if parsed_resume is None:
        parsed_resume = {}
    if experience_range is None:
        experience_range = {}

    # Build lookup: normalized skill name → assessment
    skill_map = {}
    for a in assessments:
        skill_map[_normalize_skill(a.name)] = a

    # Extract impact markers for bonus scoring
    impact_markers = _extract_impact_markers(parsed_resume)

    breakdown = {}
    matched = 0
    total_required = len(required_skills) or 1
    depth_scores = []
    strengths = []
    gaps = []

    for req in required_skills:
        skill_name = req.get("skill", "")
        min_depth = req.get("min_depth", 2)
        weight = req.get("weight", 1.0)

        assessment = skill_map.get(_normalize_skill(skill_name))

        if assessment and assessment.estimated_depth > 0:
            # Apply recency weighting
            recency = _compute_recency_factor(assessment, parsed_resume)
            effective_depth = assessment.estimated_depth * recency

            meets_depth = assessment.estimated_depth >= min_depth
            # Human-readable depth labels
            depth_label = _depth_label(assessment.estimated_depth)
            required_label = _depth_label(min_depth)
            confidence_pct = f"{assessment.depth_confidence:.0%}"
            reasoning_short = assessment.depth_reasoning[:120] if assessment.depth_reasoning else ""

            if meets_depth:
                matched += 1
                if assessment.estimated_depth >= min_depth + 1:
                    strengths.append(
                        f"Exceeds requirement in {skill_name}: "
                        f"rated {depth_label} (depth {assessment.estimated_depth}), above the {required_label} minimum (depth {min_depth}). "
                        f"{confidence_pct} confidence. {reasoning_short}"
                    )
                else:
                    strengths.append(
                        f"Meets requirement in {skill_name}: "
                        f"rated {depth_label} (depth {assessment.estimated_depth}), matching the required {required_label} level (depth {min_depth}). "
                        f"{confidence_pct} confidence. {reasoning_short}"
                    )
            else:
                shortfall = min_depth - assessment.estimated_depth
                gaps.append(
                    f"Below requirement in {skill_name}: "
                    f"rated {depth_label} (depth {assessment.estimated_depth}), but the role needs {required_label} level (depth {min_depth}). "
                    f"The candidate is {shortfall} level{'s' if shortfall > 1 else ''} below the requirement. "
                    f"{reasoning_short}"
                )

            depth_scores.append(min(effective_depth / max(min_depth, 1), 1.0) * weight)
            breakdown[skill_name] = {
                "required_depth": min_depth,
                "estimated_depth": assessment.estimated_depth,
                "matched_skill": assessment.name,
                "match": meets_depth,
                "confidence": assessment.depth_confidence,
                "weight": weight,
                "recency_factor": round(recency, 2),
                "reasoning": _sanitize_text(reasoning_short),
            }
        else:
            # Skill completely missing from resume
            required_label = _depth_label(min_depth)
            gaps.append(
                f"Missing required skill: {skill_name}. "
                f"This role requires {required_label} level (depth {min_depth}), "
                f"but no evidence of {skill_name} was found anywhere on the resume. "
                f"This is a hard gap that will likely need training or prior experience."
            )
            depth_scores.append(0.0)
            breakdown[skill_name] = {
                "required_depth": min_depth,
                "estimated_depth": 0,
                "matched_skill": None,
                "match": False,
                "confidence": 0.0,
                "weight": weight,
                "recency_factor": 0.0,
                "reasoning": f"No evidence of {skill_name} found on resume.",
            }

    # Preferred skills
    preferred_matched = 0
    total_preferred = len(preferred_skills) or 1
    for pref in preferred_skills:
        skill_name = pref.get("skill", "")
        assessment = skill_map.get(_normalize_skill(skill_name))
        if assessment and assessment.estimated_depth > 0:
            preferred_matched += 1
            depth_label = _depth_label(assessment.estimated_depth)
            confidence_pct = f"{assessment.depth_confidence:.0%}"
            reasoning_short = assessment.depth_reasoning[:120] if assessment.depth_reasoning else ""
            strengths.append(
                f"Has preferred skill: {skill_name}, "
                f"rated {depth_label} (depth {assessment.estimated_depth}). "
                f"{confidence_pct} confidence. "
                f"This is a nice to have that strengthens the candidacy. {reasoning_short}"
            )
            breakdown[skill_name] = {
                "required_depth": 0,
                "estimated_depth": assessment.estimated_depth,
                "matched_skill": assessment.name,
                "match": True,
                "confidence": assessment.depth_confidence,
                "weight": 0.5,
                "preferred": True,
                "reasoning": _sanitize_text(reasoning_short),
            }

    # ── Experience range validation ─────────────────────────────────
    experience_penalty = 0.0
    candidate_years = _estimate_candidate_years(parsed_resume)
    min_years = experience_range.get("min_years") if experience_range else None
    max_years = experience_range.get("max_years") if experience_range else None

    if candidate_years is not None and min_years is not None:
        if candidate_years < min_years:
            shortfall = min_years - candidate_years
            # Graduated penalty: 0.03 per year short, capped at 0.15
            experience_penalty = min(shortfall * 0.03, 0.15)
            gaps.append(
                f"Experience shortfall: candidate has approximately {candidate_years} years of experience, "
                f"but this role requires a minimum of {min_years} years. "
                f"The candidate is roughly {shortfall} year{'s' if shortfall != 1 else ''} short of the requirement."
            )
        elif max_years is not None and candidate_years > max_years + 5:
            # Significantly overqualified: mild flag (not a hard penalty)
            gaps.append(
                f"Potentially overqualified: candidate has approximately {candidate_years} years of experience, "
                f"but this role targets {min_years} to {max_years} years. "
                f"Consider whether seniority expectations and compensation align."
            )

    # ── Leadership / architecture signal detection ────────────────
    leadership_bonus = 0.0
    seniority_info = detect_seniority(job_title, "")
    if seniority_info.get("level") in ("principal", "staff", "senior", "lead", "architect"):
        leadership_signals = _detect_leadership_signals(parsed_resume)
        if leadership_signals:
            leadership_bonus = min(len(leadership_signals) * 0.01, 0.04)
            for signal in leadership_signals[:3]:
                strengths.append(signal)
        else:
            gaps.append(
                f"No leadership or architecture signals detected for a {seniority_info['level']} level role. "
                f"The resume lacks evidence of mentoring, team leadership, architecture ownership, "
                f"or system design at scale. Probe these areas in the interview."
            )

    # ── Compute aggregate scores ──────────────────────────────────────

    skill_match = matched / total_required if required_skills else 0.5
    depth_avg = sum(depth_scores) / len(depth_scores) if depth_scores else 0.5

    # Experience score: confidence-weighted depth with recency
    matched_assessments = [a for a in assessments if a.estimated_depth > 0]
    if matched_assessments:
        weighted_sum = sum(
            a.estimated_depth * a.depth_confidence * _compute_recency_factor(a, parsed_resume)
            for a in matched_assessments
        )
        weight_total = sum(a.depth_confidence for a in matched_assessments)
        avg_weighted_depth = weighted_sum / weight_total if weight_total > 0 else 0
        experience_score = min(avg_weighted_depth / 5.0, 1.0)
    else:
        experience_score = 0.0

    # Education score: uses actual parsed education data
    education_score = _compute_education_score(assessments, required_skills, parsed_resume)

    # Preferred skills bonus
    preferred_bonus = (preferred_matched / total_preferred) * 0.08 if preferred_skills else 0.0

    # Impact bonus: candidates with quantified achievements get a small boost (0-0.03)
    impact_bonus = min(len(impact_markers) * 0.005, 0.03)

    # Overall score: weighted composite
    # Weights: skill_match 33%, depth 23%, experience 20%, education 10%, preferred up to 8%,
    #          impact up to 3%, perfect match 3%, leadership up to 4%
    # Penalty: experience shortfall up to -15%
    overall = (
        (skill_match * 0.33) +
        (depth_avg * 0.23) +
        (experience_score * 0.20) +
        (education_score * 0.10) +
        (preferred_bonus) +
        (impact_bonus) +
        (leadership_bonus) +
        (0.03 if matched == total_required else 0.0) -
        experience_penalty
    )
    overall = min(overall, 1.0)

    # Recommendation thresholds
    if overall >= 0.75:
        recommendation = "strong_yes"
    elif overall >= 0.60:
        recommendation = "yes"
    elif overall >= 0.40:
        recommendation = "maybe"
    elif overall >= 0.25:
        recommendation = "no"
    else:
        recommendation = "strong_no"

    return {
        "overall": round(overall, 3),
        "skill_match": round(skill_match, 3),
        "experience": round(experience_score, 3),
        "education": round(education_score, 3),
        "depth": round(depth_avg, 3),
        "preferred_bonus": round(preferred_bonus, 3),
        "impact_bonus": round(impact_bonus, 3),
        "impact_markers": len(impact_markers),
        "breakdown": breakdown,
        "strengths": [_sanitize_text(s) for s in strengths],
        "gaps": [_sanitize_text(g) for g in gaps],
        "recommendation": recommendation,
    }


def _estimate_candidate_years(parsed_resume: dict) -> int | None:
    """
    Estimate total years of professional experience from parsed resume data.
    Returns None if we can't determine experience.
    """
    experiences = parsed_resume.get("experience", [])
    if not experiences:
        return None

    current_year = datetime.now().year
    earliest_start = None
    latest_end = current_year

    for exp in experiences:
        start = exp.get("start_date", "")
        end = exp.get("end_date", "")

        start_match = re.search(r'(20\d{2}|19\d{2})', str(start))
        if start_match:
            year = int(start_match.group(1))
            if earliest_start is None or year < earliest_start:
                earliest_start = year

        if end and "present" not in str(end).lower() and "current" not in str(end).lower():
            end_match = re.search(r'(20\d{2}|19\d{2})', str(end))
            if end_match:
                year = int(end_match.group(1))
                if year > latest_end:
                    latest_end = year

    if earliest_start is None:
        return None

    return max(latest_end - earliest_start, 0)


def _detect_leadership_signals(parsed_resume: dict) -> list:
    """
    Detect leadership, architecture, and mentoring signals in the resume.
    Returns a list of strength messages for detected signals.
    """
    signals = []
    experiences = parsed_resume.get("experience", [])

    leadership_keywords = {
        "led", "lead", "leading", "managed", "mentored", "mentoring",
        "architected", "designed system", "system design", "technical lead",
        "tech lead", "team lead", "principal engineer", "staff engineer",
        "directed", "oversaw", "supervised", "coordinated team",
        "cross functional", "cross team", "org wide", "company wide",
        "scaled", "scaling", "platform", "infrastructure owner",
        "technical strategy", "roadmap", "architecture review",
    }

    title_signals = {
        "lead", "senior", "principal", "staff", "architect", "director",
        "head of", "vp ", "manager", "chief",
    }

    for exp in experiences:
        title = (exp.get("title", "") or "").lower()
        description = (exp.get("description", "") or "").lower()
        company = exp.get("company", "Unknown")

        # Check titles for leadership roles
        for kw in title_signals:
            if kw in title:
                signals.append(
                    f"Leadership role signal: held title '{exp.get('title', '')}' at {company}, "
                    f"suggesting experience with leadership or senior responsibilities."
                )
                break

        # Check descriptions for leadership activities
        found_in_desc = []
        for kw in leadership_keywords:
            if kw in description:
                found_in_desc.append(kw)
        if found_in_desc:
            signals.append(
                f"Leadership activity detected at {company}: "
                f"resume mentions {', '.join(found_in_desc[:3])}, "
                f"indicating hands on leadership or architecture experience."
            )

    # Deduplicate and cap
    seen = set()
    unique = []
    for s in signals:
        key = s[:50]
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique[:5]


def _sanitize_text(text: str) -> str:
    """Remove all dashes, emdashes, and endashes from output text.
    VetLayer results must never contain these characters."""
    return text.replace("—", ", ").replace("–", ", ").replace(" - ", ", ")


def _generate_summary(candidate, job, assessments, scores) -> str:
    """
    Generate a clear, recruiter-friendly summary of the analysis.
    Written in plain language with no dashes or emdashes.
    Designed to give a quick verdict that helps recruiters make staging decisions.
    """
    matched_skills = [a for a in assessments if a.estimated_depth > 0]
    avg_depth = sum(a.estimated_depth for a in matched_skills) / len(matched_skills) if matched_skills else 0
    high_depth = [a for a in matched_skills if a.estimated_depth >= 4]

    company_part = f" at {job.company}" if job.company else ""
    summary = f"{candidate.name} was evaluated for the {job.title} role{company_part}. "

    matched_count = sum(1 for s in scores.get("breakdown", {}).values() if s.get("match"))
    total_breakdown = len([k for k in scores.get("breakdown", {}) if not k.startswith("_")])

    if total_breakdown > 0:
        coverage_pct = round((matched_count / total_breakdown) * 100)
        summary += (
            f"Out of {total_breakdown} evaluated skills, {matched_count} met or exceeded "
            f"the required depth ({coverage_pct}% coverage). "
        )

    if matched_skills:
        summary += (
            f"The average proficiency across matched skills was {avg_depth:.1f} out of 5. "
        )

    if high_depth:
        top_names = ", ".join(a.name for a in high_depth[:4])
        summary += f"Notably strong in {top_names}. "

    # Impact callout
    impact_count = scores.get("impact_markers", 0)
    if impact_count >= 3:
        summary += f"The resume includes {impact_count} quantified impact statements, demonstrating measurable contributions. "

    gap_count = len(scores.get("gaps", []))
    if gap_count == 0:
        summary += "No skill gaps were identified. "
    elif gap_count <= 2:
        gap_skills = []
        for g in scores["gaps"][:2]:
            skill_part = g.split(":")[0].replace("Missing required skill", "").replace("Below requirement in", "").strip()
            if skill_part:
                gap_skills.append(skill_part)
        if gap_skills:
            summary += f"Areas to probe further: {', '.join(gap_skills)}. "
    else:
        summary += f"{gap_count} skill gaps were identified that may need further evaluation. "

    rec_map = {
        "strong_yes": "This candidate is a strong match and is recommended to advance to the next stage.",
        "yes": "This candidate is a good fit and is recommended to proceed in the pipeline.",
        "maybe": "This candidate shows potential but has some gaps. Consider a focused technical screen to verify key areas before advancing.",
        "no": "This candidate does not appear to be a strong fit for this role based on the skill requirements.",
        "strong_no": "There is a significant mismatch between this candidate's profile and the role requirements.",
    }
    summary += rec_map.get(scores["recommendation"], "Review pending.")

    return _sanitize_text(summary)
