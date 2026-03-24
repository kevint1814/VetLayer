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
from app.services.role_type_detector import detect_role_type
from app.services.experience_trajectory import analyze_trajectory, _get_seniority_level
from app.services.soft_skill_detector import detect_soft_skill_proxies, get_soft_skill_gaps_for_role
from app.services.dynamic_taxonomy import (
    generate_skill_taxonomy, generate_batch_taxonomies,
    get_dynamic_evidence_aliases, is_dynamically_generated,
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

    # ── Detect role type for adaptive scoring ─────────────────────────
    role_type_info = detect_role_type(
        job_title=job.title or "",
        job_description=job.description or "",
        required_skills=required_skills,
        preferred_skills=preferred_skills,
    )
    logger.info(f"Role type: {role_type_info['type']} (confidence={role_type_info['confidence']:.2f})")

    # ── Analyze experience trajectory ─────────────────────────────────
    trajectory_info = analyze_trajectory(
        parsed_resume=resume_parsed,
        target_job_title=job.title or "",
    )
    logger.info(f"Trajectory score: {trajectory_info['trajectory_score']}, type={trajectory_info['progression_type']}")

    # ── Detect soft skill proxies ─────────────────────────────────────
    soft_skill_info = detect_soft_skill_proxies(resume_parsed)
    logger.info(f"Soft skill score: {soft_skill_info['soft_skill_score']}, strongest={soft_skill_info['strongest_areas']}")

    # ── Assess domain fit ──────────────────────────────────────────────
    from app.services.domain_fit import assess_domain_fit
    domain_fit_info = assess_domain_fit(
        job_title=job.title or "",
        job_description=job.description or "",
        parsed_resume=resume_parsed,
        required_skills=required_skills,
    )
    logger.info(f"Domain fit: {domain_fit_info['domain_match']} (domain={domain_fit_info['jd_domain']}, score={domain_fit_info['domain_fit_score']})")

    # ── Generate dynamic taxonomies for unknown skills ────────────────
    # Use skill ontology first (decoupled from evidence aliases), then
    # fall back to evidence aliases for coverage of tech-only skills
    from app.services.skill_ontology import resolve_skill as _ontology_resolve
    from app.services.skill_pipeline import _EVIDENCE_ALIASES
    taxonomy_skills = set(_EVIDENCE_ALIASES.keys())
    unknown_skills = []
    for s in (required_skills or []) + (preferred_skills or []):
        skill_name = s.get("skill", "").lower().strip()
        if not skill_name:
            continue
        # Known if in ontology OR in evidence aliases
        known_in_ontology = _ontology_resolve(skill_name) is not None
        known_in_aliases = (skill_name in taxonomy_skills or
                            any(skill_name in aliases for aliases in _EVIDENCE_ALIASES.values()))
        if not known_in_ontology and not known_in_aliases:
            unknown_skills.append(s.get("skill", ""))
    if unknown_skills:
        logger.info(f"Generating dynamic taxonomies for {len(unknown_skills)} unknown skills: {unknown_skills}")
        try:
            await generate_batch_taxonomies(unknown_skills, job_title=job.title or "", job_description=job.description or "")
        except Exception as e:
            logger.warning(f"Dynamic taxonomy generation failed (non-fatal): {e}")

    # ── Run Job-Focused Skill Assessment ──────────────────────────────
    logger.info(f"Running analysis: candidate={candidate.name}, job={job.title}")
    logger.info(f"Assessing {len(required_skills)} required + {len(preferred_skills)} preferred skills")

    try:
        assessments, pipeline_timings = await skill_pipeline.run(
            parsed_resume=resume_parsed,
            required_skills=required_skills,
            preferred_skills=preferred_skills,
            job_title=job.title or "",
            role_type=role_type_info.get("type", "hybrid"),
            domain_profile=role_type_info.get("signals", {}).get("domain_profile"),
        )
    except Exception as e:
        logger.error(f"Skill pipeline failed: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis pipeline failed: {str(e)}")

    # ── Apply skill adjacency boosts ──────────────────────────────────
    _apply_adjacency_boosts(assessments, parsed_resume=resume_parsed)

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
        role_type=role_type_info,
        trajectory=trajectory_info,
        soft_skills=soft_skill_info,
    )

    # Include pipeline timings in the breakdown for transparency
    scores["breakdown"]["_pipeline_timings"] = timings_to_dict(pipeline_timings)

    processing_time = int((time.time() - start_time) * 1000)

    # ── Generate risk flags ───────────────────────────────────────────
    risk_flags = _generate_risk_flags(
        assessments, resume_parsed, scores,
        experience_range=job.experience_range,
        job_title=job.title or "",
        role_type=role_type_info,
        soft_skills=soft_skill_info,
        trajectory=trajectory_info,
        domain_fit=domain_fit_info,
    )

    # ── Generate interview questions ──────────────────────────────────
    interview_questions = _generate_interview_questions(
        assessments, scores, required_skills, risk_flags,
        resume_parsed=resume_parsed,
        candidate_name=candidate.name or "the candidate",
        job_title=job.title or "this role",
        role_type=scores.get("role_type", "skill_heavy"),
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
        skill_breakdown={
            **scores["breakdown"],
            "_score_weights": scores.get("score_weights", {}),
            "_score_drivers": scores.get("score_drivers", []),
            "_analysis_confidence": scores.get("analysis_confidence", 0),
            "_role_type": scores.get("role_type", "skill_heavy"),
            "_role_type_confidence": scores.get("role_type_confidence", 0),
            "_role_type_signals": scores.get("role_type_signals", {}),
            "_trajectory": scores.get("trajectory", {}),
            "_soft_skill_proxies": scores.get("soft_skill_proxies", {}),
            "_domain_fit": domain_fit_info,
        },
        strengths=scores["strengths"],
        gaps=scores["gaps"],
        summary_text=_generate_summary(candidate, job, assessments, scores, domain_fit=domain_fit_info),
        recommendation=scores["recommendation"],
        llm_model_used=(
            settings.GROQ_MODEL if settings.LLM_PROVIDER == "groq"
            else settings.OPENAI_MODEL if settings.LLM_PROVIDER == "openai"
            else settings.ANTHROPIC_MODEL
        ),
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

        # Extract universal scoring metadata from skill_breakdown
        sb = (analysis.skill_breakdown if analysis else {}) or {}
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
                # Universal scoring fields
                "role_type": sb.get("_role_type", "skill_heavy"),
                "role_type_confidence": sb.get("_role_type_confidence", 0),
                "trajectory": sb.get("_trajectory", {}),
                "soft_skill_proxies": sb.get("_soft_skill_proxies", {}),
                "score_drivers": sb.get("_score_drivers", []),
                "score_weights": sb.get("_score_weights", {}),
                "domain_fit": sb.get("_domain_fit", {}),
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
    # ── Web fundamentals ──────────────────────────────────────────────
    (["html", "html5", "html 5"], "html"),
    (["css", "css3", "css 3", "cascading style sheets"], "css"),
    (["sass", "scss", "less", "stylus"], "sass/scss"),
    (["javascript", "js", "ecmascript", "es6", "es2015", "es2016", "es2017",
      "object oriented javascript", "vanilla js", "vanilla javascript"], "javascript"),
    (["typescript", "ts"], "typescript"),
    # ── Frontend frameworks ───────────────────────────────────────────
    (["react", "react.js", "reactjs", "react js"], "react"),
    (["vue", "vue.js", "vuejs", "vue js", "vue 3", "vue 2"], "vue"),
    (["angular", "angular.js", "angularjs", "angular 2+"], "angular"),
    (["next", "next.js", "nextjs"], "next.js"),
    (["nuxt", "nuxt.js", "nuxtjs"], "nuxt.js"),
    (["svelte", "sveltekit", "svelte kit"], "svelte"),
    (["gatsby", "gatsby.js", "gatsbyjs"], "gatsby"),
    (["remix", "remix.run"], "remix"),
    (["jquery", "j query"], "jquery"),
    (["tailwind", "tailwind css", "tailwindcss"], "tailwind"),
    (["bootstrap", "bootstrap css", "bootstrap 5", "bootstrap 4"], "bootstrap"),
    (["material ui", "material-ui", "mui"], "material ui"),
    # ── Backend frameworks ────────────────────────────────────────────
    (["node", "node.js", "nodejs", "node js"], "node.js"),
    (["python", "py", "python3", "python 3"], "python"),
    (["java", "jdk", "j2ee", "jee"], "java"),
    (["golang", "go lang"], "go"),
    (["php", "php7", "php8", "php 7", "php 8"], "php"),
    (["ruby", "rb"], "ruby"),
    (["c#", "csharp", "c sharp", "c #"], "c#"),
    (["c++", "cpp", "c plus plus"], "c++"),
    (["c", "c language", "ansi c"], "c"),
    (["rust", "rustlang"], "rust"),
    (["scala", "scala lang"], "scala"),
    (["kotlin", "kt"], "kotlin"),
    (["swift", "swift lang", "swift ui", "swiftui"], "swift"),
    (["dart", "dart lang"], "dart"),
    (["r", "r language", "r programming", "rlang"], "r"),
    (["perl", "perl 5"], "perl"),
    (["lua", "lua lang"], "lua"),
    (["elixir", "elixir lang"], "elixir"),
    (["haskell"], "haskell"),
    (["clojure"], "clojure"),
    # ── Backend frameworks (specific) ─────────────────────────────────
    (["fastapi", "fast api", "fast-api"], "fastapi"),
    (["django", "django rest framework", "drf", "django rest"], "django"),
    (["flask", "flask api"], "flask"),
    (["express", "express.js", "expressjs"], "express"),
    (["nestjs", "nest.js", "nest js"], "nestjs"),
    (["spring", "spring boot", "spring framework", "springboot"], "spring boot"),
    (["laravel", "laravel php"], "laravel"),
    (["symfony", "symfony php"], "symfony"),
    (["codeigniter", "code igniter"], "codeigniter"),
    (["wordpress", "word press", "wp"], "wordpress"),
    (["drupal"], "drupal"),
    (["rails", "ruby on rails", "ror"], "rails"),
    (["asp.net", "aspnet", "asp net", ".net", "dotnet", "dot net", ".net core", "dotnet core"], ".net"),
    (["sinatra"], "sinatra"),
    (["gin", "gin-gonic"], "gin"),
    (["fiber", "gofiber"], "fiber"),
    (["actix", "actix-web"], "actix"),
    (["phoenix", "phoenix framework"], "phoenix"),
    # ── Mobile ────────────────────────────────────────────────────────
    (["react native", "react-native", "reactnative", "rn"], "react native"),
    (["flutter", "flutter sdk"], "flutter"),
    (["android", "android sdk", "android development"], "android"),
    (["ios", "ios development", "ios sdk", "uikit", "cocoa touch"], "ios"),
    (["xamarin", "maui", ".net maui"], "xamarin"),
    (["ionic", "ionic framework"], "ionic"),
    (["expo", "expo sdk"], "expo"),
    # ── Databases ─────────────────────────────────────────────────────
    (["postgresql", "postgres", "pg", "psql"], "postgresql"),
    (["mongodb", "mongo", "mongoose"], "mongodb"),
    (["mysql", "mariadb"], "mysql"),
    (["redis", "redis cache", "redis db"], "redis"),
    (["sql", "structured query language", "t-sql", "tsql", "pl/sql", "plsql"], "sql"),
    (["sqlite", "sqlite3"], "sqlite"),
    (["oracle", "oracle db", "oracle database"], "oracle"),
    (["cassandra", "apache cassandra"], "cassandra"),
    (["dynamodb", "dynamo db", "aws dynamodb"], "dynamodb"),
    (["couchdb", "couch db", "couchbase"], "couchbase"),
    (["neo4j", "neo 4j", "graph database"], "neo4j"),
    (["elasticsearch", "elastic search", "elastic", "opensearch"], "elasticsearch"),
    (["firestore", "firebase firestore", "cloud firestore"], "firestore"),
    (["firebase", "firebase realtime", "firebase rtdb"], "firebase"),
    (["supabase"], "supabase"),
    (["prisma", "prisma orm"], "prisma"),
    (["sequelize"], "sequelize"),
    (["sqlalchemy", "sql alchemy"], "sqlalchemy"),
    (["typeorm", "type orm"], "typeorm"),
    # ── Messaging / Streaming ─────────────────────────────────────────
    (["kafka", "apache kafka", "kafka streams"], "kafka"),
    (["rabbitmq", "rabbit mq", "amqp"], "rabbitmq"),
    (["sqs", "aws sqs", "amazon sqs"], "sqs"),
    (["celery", "celery task"], "celery"),
    (["nats", "nats streaming"], "nats"),
    # ── DevOps / Cloud ────────────────────────────────────────────────
    (["aws", "amazon web services", "amazon aws"], "aws"),
    (["gcp", "google cloud", "google cloud platform"], "gcp"),
    (["azure", "microsoft azure", "azure cloud"], "azure"),
    (["kubernetes", "k8s", "kube"], "kubernetes"),
    (["docker", "containerization", "docker compose", "docker-compose"], "docker"),
    (["ci/cd", "ci cd", "cicd", "continuous integration", "continuous deployment",
      "github actions", "gitlab ci", "jenkins", "circleci", "travis ci",
      "azure devops", "bitbucket pipelines"], "ci/cd"),
    (["terraform", "infrastructure as code", "iac", "terraform cloud"], "terraform"),
    (["ansible", "ansible playbook"], "ansible"),
    (["nginx", "nginx server", "reverse proxy"], "nginx"),
    (["apache", "apache server", "httpd", "apache httpd"], "apache"),
    (["linux", "ubuntu", "centos", "debian", "rhel", "fedora",
      "unix", "bash scripting", "shell scripting"], "linux"),
    (["prometheus", "prometheus monitoring"], "prometheus"),
    (["grafana", "grafana dashboard"], "grafana"),
    (["datadog", "data dog"], "datadog"),
    (["new relic", "newrelic"], "new relic"),
    (["cloudflare", "cloud flare"], "cloudflare"),
    (["vercel", "vercel platform"], "vercel"),
    (["netlify"], "netlify"),
    (["heroku"], "heroku"),
    (["digitalocean", "digital ocean"], "digitalocean"),
    # ── AWS services (specific) ───────────────────────────────────────
    (["ec2", "aws ec2", "amazon ec2"], "ec2"),
    (["s3", "aws s3", "amazon s3"], "s3"),
    (["lambda", "aws lambda", "serverless"], "lambda"),
    (["ecs", "aws ecs", "fargate", "aws fargate"], "ecs"),
    (["rds", "aws rds", "amazon rds"], "rds"),
    (["cloudformation", "aws cloudformation", "cfn"], "cloudformation"),
    # ── Data / ML / AI ────────────────────────────────────────────────
    (["pandas", "pd", "python pandas"], "pandas"),
    (["numpy", "np", "python numpy"], "numpy"),
    (["scipy", "sci py"], "scipy"),
    (["matplotlib", "pyplot"], "matplotlib"),
    (["tensorflow", "tf", "tensor flow"], "tensorflow"),
    (["pytorch", "torch", "py torch"], "pytorch"),
    (["scikit-learn", "sklearn", "scikit learn"], "scikit-learn"),
    (["keras", "tf.keras"], "keras"),
    (["opencv", "open cv", "cv2"], "opencv"),
    (["spark", "apache spark", "pyspark"], "spark"),
    (["hadoop", "apache hadoop", "hdfs", "mapreduce"], "hadoop"),
    (["airflow", "apache airflow"], "airflow"),
    (["dbt", "data build tool"], "dbt"),
    (["tableau", "tableau desktop", "tableau server"], "tableau"),
    (["power bi", "powerbi", "power-bi", "microsoft power bi"], "power bi"),
    (["jupyter", "jupyter notebook", "jupyterlab"], "jupyter"),
    (["nlp", "natural language processing", "text mining"], "nlp"),
    (["computer vision", "image recognition", "object detection"], "computer vision"),
    (["llm", "large language model", "large language models", "gpt", "chatgpt",
      "openai api", "anthropic api", "claude api", "langchain", "llamaindex"], "llm/ai"),
    (["machine learning", "ml", "deep learning", "dl", "neural networks"], "machine learning"),
    (["data science", "data analysis", "data analytics"], "data science"),
    # ── Testing ───────────────────────────────────────────────────────
    (["jest", "jest js"], "jest"),
    (["pytest", "py.test", "python pytest"], "pytest"),
    (["junit", "j unit"], "junit"),
    (["cypress", "cypress.io"], "cypress"),
    (["playwright", "ms playwright"], "playwright"),
    (["selenium", "selenium webdriver"], "selenium"),
    (["mocha", "mocha js"], "mocha"),
    (["chai", "chai js"], "chai"),
    (["vitest"], "vitest"),
    (["rspec", "r spec"], "rspec"),
    (["phpunit", "php unit"], "phpunit"),
    (["testing library", "react testing library", "rtl"], "testing library"),
    (["storybook", "story book"], "storybook"),
    (["unit testing", "unit tests"], "unit testing"),
    (["integration testing", "integration tests"], "integration testing"),
    (["e2e testing", "end to end testing", "end-to-end testing"], "e2e testing"),
    (["tdd", "test driven development", "test-driven development"], "tdd"),
    # ── Tools / Build ─────────────────────────────────────────────────
    (["webpack", "module bundlers", "module bundler", "vite", "rollup", "esbuild",
      "parcel", "snowpack"], "webpack"),
    (["git", "github", "gitlab", "version control", "bitbucket"], "git"),
    (["npm", "yarn", "pnpm", "package manager"], "npm"),
    (["jira", "jira software"], "jira"),
    (["confluence"], "confluence"),
    (["figma", "figma design"], "figma"),
    (["sketch", "sketch app"], "sketch"),
    (["adobe xd", "xd"], "adobe xd"),
    (["postman", "postman api"], "postman"),
    (["swagger", "openapi", "open api", "openapi spec"], "swagger"),
    (["vs code", "vscode", "visual studio code"], "vscode"),
    (["intellij", "intellij idea", "webstorm", "pycharm"], "jetbrains"),
    # ── Concepts / Architecture ───────────────────────────────────────
    (["oop", "object oriented programming", "object-oriented"], "oop"),
    (["rest api", "restful", "restful api", "rest apis", "restful apis"], "rest api"),
    (["graphql", "graph ql"], "graphql"),
    (["microservices", "micro services", "service oriented architecture", "soa"], "microservices"),
    (["agile", "scrum", "kanban", "agile/scrum", "agile methodology"], "agile"),
    (["design patterns", "solid principles", "solid", "clean architecture"], "design patterns"),
    (["system design", "systems design", "distributed systems", "scalability"], "system design"),
    (["api design", "api development", "api architecture"], "api design"),
    (["responsive design", "responsive web design", "rwd", "mobile first"], "responsive design"),
    (["seo", "search engine optimization"], "seo"),
    (["accessibility", "a11y", "wcag", "web accessibility", "aria"], "accessibility"),
    (["security", "web security", "owasp", "authentication", "authorization",
      "oauth", "oauth2", "jwt", "json web token"], "security"),
    (["websocket", "websockets", "socket.io", "real-time", "real time communication"], "websockets"),
    # ── General tools (non-technical but recruiter-listed) ────────────
    (["microsoft office", "ms office", "office 365", "microsoft 365",
      "word", "excel", "powerpoint", "outlook", "ms word", "ms excel",
      "ms powerpoint", "office suite"], "microsoft office"),
    (["google workspace", "g suite", "gsuite", "google docs", "google sheets",
      "google slides"], "google workspace"),
    (["ai tools", "ai-powered tools", "ai assistants", "copilot",
      "github copilot", "chatgpt", "claude", "generative ai", "gen ai",
      "ai automation", "prompt engineering"], "ai tools"),
    (["photoshop", "adobe photoshop", "illustrator", "adobe illustrator",
      "lightroom", "indesign", "after effects", "premiere pro",
      "adobe creative suite", "creative cloud"], "adobe creative suite"),
    (["canva"], "canva"),
    (["slack", "microsoft teams", "teams", "zoom", "discord"], "collaboration tools"),
    # ── Browser APIs ──────────────────────────────────────────────────
    (["browser apis", "web apis", "browser api", "web storage",
      "local storage", "localstorage", "session storage", "sessionstorage",
      "indexeddb", "indexed db", "service worker", "service workers",
      "web workers", "fetch api", "xmlhttprequest", "xhr",
      "dom manipulation", "dom api", "dom apis", "web components",
      "shadow dom", "custom elements", "intersection observer",
      "mutation observer", "resize observer", "performance api",
      "geolocation api", "notification api", "websocket api",
      "canvas api", "webgl", "web audio", "media api",
      "clipboard api", "drag and drop", "file api",
      "history api", "url api", "broadcast channel",
      "cache api", "browser caching",
      "web storage api", "web platform"], "browser apis"),
    # ── ERP / Enterprise ───────────────────────────────────────────────
    (["sap", "sap erp", "sap s/4hana", "sap hana"], "sap"),
    (["oracle erp", "oracle cloud", "oracle fusion"], "oracle erp"),
    (["netsuite", "oracle netsuite"], "netsuite"),
    (["microsoft dynamics", "dynamics 365", "dynamics crm", "dynamics nav"], "microsoft dynamics"),
    (["workday", "workday hcm"], "workday"),
    (["servicenow", "service now", "snow"], "servicenow"),
    # ── CRM ────────────────────────────────────────────────────────────
    (["salesforce", "sfdc", "salesforce crm", "salesforce lightning",
      "apex", "soql", "visualforce"], "salesforce"),
    (["hubspot", "hub spot", "hubspot crm"], "hubspot"),
    (["zoho", "zoho crm"], "zoho"),
    # ── Blockchain / Web3 ──────────────────────────────────────────────
    (["solidity", "solidity lang"], "solidity"),
    (["ethereum", "eth", "evm"], "ethereum"),
    (["web3", "web3.js", "web3js", "ethers.js", "ethersjs"], "web3"),
    (["hardhat", "truffle", "foundry"], "hardhat"),
    (["smart contracts", "smart contract"], "smart contracts"),
    # ── Game Development ───────────────────────────────────────────────
    (["unity", "unity3d", "unity 3d", "unity engine"], "unity"),
    (["unreal", "unreal engine", "ue4", "ue5"], "unreal engine"),
    (["godot", "godot engine"], "godot"),
    (["c# unity", "game development", "gamedev"], "game development"),
    # ── Low-code / No-code ─────────────────────────────────────────────
    (["zapier", "zap"], "zapier"),
    (["make", "integromat", "make.com"], "make"),
    (["retool", "re tool"], "retool"),
    (["power apps", "powerapps", "microsoft power apps"], "power apps"),
    (["power automate", "power automate flow", "microsoft power automate"], "power automate"),
    (["bubble", "bubble.io"], "bubble"),
    (["airtable", "air table"], "airtable"),
    (["notion", "notion.so"], "notion"),
    # ── Business Intelligence ──────────────────────────────────────────
    (["looker", "google looker", "looker studio"], "looker"),
    (["qlik", "qlikview", "qlik sense", "qliksense"], "qlik"),
    (["google data studio", "data studio"], "google data studio"),
    (["metabase", "meta base"], "metabase"),
    (["superset", "apache superset"], "superset"),
    (["sisense"], "sisense"),
    # ── Cybersecurity ──────────────────────────────────────────────────
    (["splunk", "splunk enterprise", "splunk siem"], "splunk"),
    (["wireshark", "wire shark"], "wireshark"),
    (["burp suite", "burpsuite", "burp"], "burp suite"),
    (["nessus", "tenable nessus"], "nessus"),
    (["metasploit", "metasploit framework"], "metasploit"),
    (["kali linux", "kali"], "kali linux"),
    (["soc2", "soc 2", "soc2 compliance"], "soc2"),
    (["iso 27001", "iso27001"], "iso 27001"),
    (["penetration testing", "pen testing", "pentest", "pentesting"], "penetration testing"),
    (["vulnerability assessment", "vulnerability scanning"], "vulnerability assessment"),
    (["siem", "security information", "security event management"], "siem"),
    (["ids/ips", "intrusion detection", "intrusion prevention"], "ids/ips"),
    # ── Networking / Infrastructure ────────────────────────────────────
    (["cisco", "cisco ios", "ccna", "ccnp"], "cisco"),
    (["load balancer", "load balancing", "haproxy", "f5"], "load balancing"),
    (["dns", "domain name system", "bind", "route53", "aws route 53"], "dns"),
    (["cdn", "content delivery network", "cloudfront", "akamai", "fastly"], "cdn"),
    (["vpn", "virtual private network", "wireguard", "openvpn"], "vpn"),
    (["tcp/ip", "tcp ip", "networking", "network protocols", "http/https",
      "osi model"], "networking"),
    # ── Data Platforms ─────────────────────────────────────────────────
    (["snowflake", "snowflake db", "snowflake data"], "snowflake"),
    (["bigquery", "big query", "google bigquery", "bq"], "bigquery"),
    (["databricks", "data bricks", "databricks lakehouse"], "databricks"),
    (["redshift", "amazon redshift", "aws redshift"], "redshift"),
    (["dbt cloud", "dbt core"], "dbt"),
    (["fivetran", "five tran"], "fivetran"),
    (["stitch", "stitch data"], "stitch"),
    (["delta lake", "deltalake"], "delta lake"),
    (["apache iceberg", "iceberg"], "iceberg"),
    (["data lake", "datalake", "data lakehouse", "lakehouse"], "data lake"),
    (["etl", "elt", "extract transform load", "data pipeline", "data pipelines"], "etl/elt"),
    # ── MLOps / LLMOps ─────────────────────────────────────────────────
    (["mlflow", "ml flow", "mlflow tracking"], "mlflow"),
    (["weights and biases", "wandb", "w&b"], "wandb"),
    (["langchain", "lang chain"], "langchain"),
    (["llamaindex", "llama index", "llama_index"], "llamaindex"),
    (["pinecone", "pinecone db"], "pinecone"),
    (["chroma", "chromadb", "chroma db"], "chroma"),
    (["weaviate", "weaviate db"], "weaviate"),
    (["milvus", "milvus db"], "milvus"),
    (["vector database", "vector db", "vector store", "vector search"], "vector database"),
    (["prompt engineering", "prompt design", "prompt tuning"], "prompt engineering"),
    (["rag", "retrieval augmented generation"], "rag"),
    (["fine tuning", "fine-tuning", "model fine tuning", "llm fine tuning"], "fine-tuning"),
    (["hugging face", "huggingface", "hf", "transformers library"], "hugging face"),
    (["sagemaker", "aws sagemaker", "amazon sagemaker"], "sagemaker"),
    (["vertex ai", "google vertex", "vertex"], "vertex ai"),
    (["azure ml", "azure machine learning"], "azure ml"),
    (["kubeflow", "kube flow"], "kubeflow"),
    (["feature store", "feast", "tecton"], "feature store"),
    # ── Project Management / Methodology ───────────────────────────────
    (["asana", "asana pm"], "asana"),
    (["monday", "monday.com", "monday pm"], "monday"),
    (["trello", "trello board"], "trello"),
    (["linear", "linear app", "linear.app"], "linear"),
    (["clickup", "click up"], "clickup"),
    (["basecamp"], "basecamp"),
    (["agile", "agile methodology", "agile development"], "agile"),
    (["scrum", "scrum master", "scrum methodology"], "scrum"),
    (["kanban", "kanban board", "kanban methodology"], "kanban"),
    (["safe", "scaled agile", "scaled agile framework", "safe framework"], "safe"),
    (["lean", "lean methodology", "lean development"], "lean"),
    (["waterfall", "waterfall methodology"], "waterfall"),
    # ── Documentation / Communication ──────────────────────────────────
    (["technical writing", "tech writing", "api documentation"], "technical writing"),
    (["latex", "tex", "overleaf"], "latex"),
    # ── Other tools ────────────────────────────────────────────────────
    (["kibana", "logstash", "elk stack", "elk"], "elk stack"),
    (["memcached", "varnish"], "caching"),
    (["activemq", "zeromq", "message queue", "message broker"], "message queue"),
    (["grpc", "g rpc", "protocol buffers", "protobuf"], "grpc"),
    (["soap", "soap api", "wsdl"], "soap"),
    (["xml", "xslt", "xpath"], "xml"),
    (["yaml", "yml"], "yaml"),
    (["json", "json api"], "json"),
    (["regex", "regular expressions", "regexp"], "regex"),
    (["bash", "shell", "zsh", "powershell", "cmd"], "shell scripting"),
    (["vim", "neovim", "emacs"], "vim"),
]

# Build lookup: variant → canonical
_SKILL_ALIASES = {}
for variants, canonical in _SKILL_GROUPS:
    for v in variants:
        _SKILL_ALIASES[v] = canonical
    _SKILL_ALIASES[canonical] = canonical


# ── Umbrella Skill Expansion ──────────────────────────────────────────
# Maps umbrella/generic terms to their constituent technical skills.
# When a job requires "HTML" and a candidate says "web development," we need
# to know that web development implies HTML/CSS/JS knowledge.
# Used in: (1) job parsing to expand vague requirements, (2) skill assessment
# to boost scores when umbrella terms are detected on resumes.
_UMBRELLA_SKILLS = {
    "web development": [
        ("html", 3), ("css", 3), ("javascript", 3), ("browser apis", 2),
    ],
    "frontend development": [
        ("html", 3), ("css", 3), ("javascript", 3), ("browser apis", 2),
        ("responsive design", 2),
    ],
    "frontend": [
        ("html", 3), ("css", 3), ("javascript", 3), ("browser apis", 2),
    ],
    "backend development": [
        ("rest api", 3), ("sql", 2),
    ],
    "backend": [
        ("rest api", 3), ("sql", 2),
    ],
    "full-stack development": [
        ("html", 3), ("css", 3), ("javascript", 3), ("rest api", 3),
        ("sql", 2), ("browser apis", 2),
    ],
    "full stack development": [
        ("html", 3), ("css", 3), ("javascript", 3), ("rest api", 3),
        ("sql", 2), ("browser apis", 2),
    ],
    "full-stack": [
        ("html", 3), ("css", 3), ("javascript", 3), ("rest api", 3),
        ("sql", 2), ("browser apis", 2),
    ],
    "mobile development": [
        ("rest api", 2),
    ],
    "mobile app development": [
        ("rest api", 2),
    ],
    "data engineering": [
        ("sql", 3), ("python", 3),
    ],
    "data analysis": [
        ("sql", 2), ("python", 2),
    ],
    "devops": [
        ("linux", 3), ("docker", 2), ("ci/cd", 2),
    ],
    "cloud engineering": [
        ("linux", 2), ("docker", 2), ("ci/cd", 2),
    ],
    "site reliability": [
        ("linux", 3), ("docker", 2), ("ci/cd", 3),
    ],
    "machine learning engineering": [
        ("python", 3), ("machine learning", 3),
    ],
    "ui/ux development": [
        ("html", 3), ("css", 3), ("javascript", 2), ("responsive design", 3),
        ("accessibility", 2),
    ],
    "cms development": [
        ("html", 3), ("css", 3), ("php", 2),
    ],
    "api development": [
        ("rest api", 3),
    ],
    "database administration": [
        ("sql", 4),
    ],
    "systems programming": [
        ("c", 3), ("linux", 3),
    ],
    "security engineering": [
        ("security", 3), ("linux", 2), ("networking", 2),
    ],
    "blockchain development": [
        ("solidity", 3), ("web3", 2), ("javascript", 2),
    ],
    "game development": [
        ("c++", 2), ("c#", 2),
    ],
    "data visualization": [
        ("sql", 2), ("python", 2),
    ],
    "etl development": [
        ("sql", 3), ("python", 3), ("etl/elt", 3),
    ],
    "cloud architecture": [
        ("linux", 2), ("docker", 2), ("kubernetes", 2), ("ci/cd", 2),
    ],
    "mlops": [
        ("python", 3), ("docker", 2), ("machine learning", 3),
    ],
    "platform engineering": [
        ("linux", 3), ("docker", 3), ("kubernetes", 3), ("ci/cd", 3),
    ],
    "solutions architecture": [
        ("rest api", 3), ("system design", 3), ("security", 2),
    ],
    "qa engineering": [
        ("unit testing", 2), ("integration testing", 2),
    ],
    "embedded systems": [
        ("c", 3), ("c++", 2), ("linux", 2),
    ],
    "network engineering": [
        ("networking", 3), ("linux", 2), ("cisco", 2),
    ],
}


# ── Skill Adjacency Graph ─────────────────────────────────────────────
# Maps canonical skill → list of (related_skill, implied_min_depth) pairs.
# When a candidate has skill A at depth X (>=3), related skills get a floor
# of implied_min_depth. Prevents false negatives for foundational skills.
_SKILL_ADJACENCY = {
    # ── Frontend frameworks → foundation ──────────────────────────────
    "react": [("html", 3), ("css", 3), ("javascript", 3), ("browser apis", 2), ("npm", 2)],
    "next.js": [("react", 3), ("html", 3), ("css", 3), ("javascript", 3), ("node.js", 2)],
    "nuxt.js": [("vue", 3), ("html", 3), ("css", 3), ("javascript", 3), ("node.js", 2)],
    "vue": [("html", 3), ("css", 3), ("javascript", 3), ("browser apis", 2)],
    "angular": [("html", 3), ("css", 3), ("javascript", 3), ("typescript", 3), ("browser apis", 2)],
    "svelte": [("html", 3), ("css", 3), ("javascript", 3)],
    "gatsby": [("react", 3), ("html", 3), ("css", 3), ("javascript", 3), ("graphql", 2)],
    "remix": [("react", 3), ("html", 3), ("css", 3), ("javascript", 3), ("node.js", 2)],
    "jquery": [("html", 2), ("css", 2), ("javascript", 2)],
    "tailwind": [("css", 3), ("html", 2)],
    "bootstrap": [("css", 2), ("html", 2)],
    "material ui": [("react", 2), ("css", 2)],
    # ── CSS preprocessors ─────────────────────────────────────────────
    "sass/scss": [("css", 3)],
    "typescript": [("javascript", 3)],
    # ── JS backend → JS ───────────────────────────────────────────────
    "node.js": [("javascript", 3), ("npm", 2)],
    "express": [("node.js", 3), ("javascript", 3), ("rest api", 2)],
    "nestjs": [("node.js", 3), ("typescript", 3), ("javascript", 3), ("rest api", 2)],
    # ── Python frameworks → Python ────────────────────────────────────
    "fastapi": [("python", 3), ("rest api", 2)],
    "django": [("python", 3), ("sql", 2), ("rest api", 2)],
    "flask": [("python", 3)],
    "celery": [("python", 3)],
    # ── PHP frameworks → PHP ──────────────────────────────────────────
    "laravel": [("php", 3), ("sql", 2), ("rest api", 2)],
    "symfony": [("php", 3), ("sql", 2)],
    "codeigniter": [("php", 3), ("sql", 2)],
    "wordpress": [("php", 2), ("html", 2), ("css", 2), ("mysql", 2)],
    "drupal": [("php", 2), ("html", 2), ("css", 2), ("mysql", 2)],
    # ── Ruby → Ruby ───────────────────────────────────────────────────
    "rails": [("ruby", 3), ("sql", 2), ("html", 2), ("css", 2), ("rest api", 2)],
    "sinatra": [("ruby", 3)],
    # ── Java frameworks → Java ────────────────────────────────────────
    "spring boot": [("java", 3), ("sql", 2), ("rest api", 2)],
    # ── .NET → C# ─────────────────────────────────────────────────────
    ".net": [("c#", 3), ("sql", 2)],
    # ── Go frameworks → Go ────────────────────────────────────────────
    "gin": [("go", 3), ("rest api", 2)],
    "fiber": [("go", 3), ("rest api", 2)],
    # ── Rust frameworks → Rust ────────────────────────────────────────
    "actix": [("rust", 3)],
    # ── Elixir → Elixir ──────────────────────────────────────────────
    "phoenix": [("elixir", 3)],
    # ── Mobile → foundation ───────────────────────────────────────────
    "react native": [("react", 3), ("javascript", 3), ("rest api", 2)],
    "flutter": [("dart", 3), ("rest api", 2)],
    "android": [("kotlin", 2), ("java", 2), ("rest api", 2)],
    "ios": [("swift", 2), ("rest api", 2)],
    "expo": [("react native", 3), ("react", 3), ("javascript", 3)],
    "ionic": [("html", 2), ("css", 2), ("javascript", 2), ("angular", 2)],
    # ── Data / ML → Python + foundation ───────────────────────────────
    "pandas": [("python", 3)],
    "numpy": [("python", 3)],
    "scipy": [("python", 3), ("numpy", 2)],
    "matplotlib": [("python", 2)],
    "tensorflow": [("python", 3), ("machine learning", 3)],
    "pytorch": [("python", 3), ("machine learning", 3)],
    "scikit-learn": [("python", 3), ("machine learning", 2)],
    "keras": [("python", 3), ("tensorflow", 2), ("machine learning", 2)],
    "opencv": [("python", 2), ("computer vision", 2)],
    "spark": [("python", 2), ("sql", 2)],
    "hadoop": [("linux", 2)],
    "airflow": [("python", 3)],
    "dbt": [("sql", 3)],
    "jupyter": [("python", 2)],
    "llm/ai": [("python", 2)],
    # ── ORM → language + SQL ──────────────────────────────────────────
    "prisma": [("sql", 2), ("typescript", 2)],
    "sequelize": [("sql", 2), ("javascript", 2)],
    "sqlalchemy": [("sql", 2), ("python", 3)],
    "typeorm": [("sql", 2), ("typescript", 2)],
    # ── DB-specific → SQL ─────────────────────────────────────────────
    "postgresql": [("sql", 3)],
    "mysql": [("sql", 3)],
    "sqlite": [("sql", 2)],
    "oracle": [("sql", 3)],
    # ── DevOps / Cloud adjacencies ────────────────────────────────────
    "kubernetes": [("docker", 3), ("linux", 2)],
    "terraform": [("linux", 2)],
    "ansible": [("linux", 3)],
    "docker": [("linux", 2)],
    "ecs": [("aws", 2), ("docker", 2)],
    "lambda": [("aws", 2)],
    "ec2": [("aws", 2), ("linux", 2)],
    "s3": [("aws", 2)],
    "rds": [("aws", 2), ("sql", 2)],
    "cloudformation": [("aws", 3)],
    "prometheus": [("linux", 2)],
    "grafana": [("linux", 2)],
    # ── API protocols ─────────────────────────────────────────────────
    "graphql": [("rest api", 2)],
    "websockets": [("javascript", 2)],
    # ── Architecture concepts ─────────────────────────────────────────
    "microservices": [("rest api", 3), ("docker", 2)],
    "system design": [("rest api", 2)],
    # ── Testing → language ────────────────────────────────────────────
    "jest": [("javascript", 3)],
    "pytest": [("python", 3)],
    "junit": [("java", 3)],
    "cypress": [("javascript", 3)],
    "playwright": [("javascript", 2)],
    "rspec": [("ruby", 3)],
    "phpunit": [("php", 3)],
    "vitest": [("javascript", 3)],
    "testing library": [("react", 2), ("javascript", 3)],
    # ── DB tools → database ───────────────────────────────────────────
    "firebase": [("javascript", 2)],
    "supabase": [("postgresql", 2), ("sql", 2)],
    "elasticsearch": [("rest api", 2)],
    # ── Blockchain → foundation ─────────────────────────────────────────
    "solidity": [("ethereum", 3), ("javascript", 2), ("smart contracts", 3)],
    "web3": [("javascript", 3), ("ethereum", 2)],
    "hardhat": [("solidity", 3), ("javascript", 2)],
    # ── Game Dev → foundation ──────────────────────────────────────────
    "unity": [("c#", 3)],
    "unreal engine": [("c++", 3)],
    "godot": [("python", 2)],
    # ── Data platforms → foundation ────────────────────────────────────
    "snowflake": [("sql", 3)],
    "bigquery": [("sql", 3), ("gcp", 2)],
    "databricks": [("python", 3), ("spark", 3), ("sql", 2)],
    "redshift": [("sql", 3), ("aws", 2)],
    "fivetran": [("sql", 2), ("etl/elt", 2)],
    "delta lake": [("spark", 3), ("python", 2)],
    # ── MLOps / LLMOps → foundation ───────────────────────────────────
    "langchain": [("python", 3), ("llm/ai", 3)],
    "llamaindex": [("python", 3), ("llm/ai", 3)],
    "mlflow": [("python", 3), ("machine learning", 2)],
    "wandb": [("python", 3), ("machine learning", 2)],
    "hugging face": [("python", 3), ("machine learning", 3), ("llm/ai", 2)],
    "sagemaker": [("aws", 3), ("python", 2), ("machine learning", 2)],
    "vertex ai": [("gcp", 3), ("python", 2), ("machine learning", 2)],
    "pinecone": [("python", 2), ("vector database", 3)],
    "chroma": [("python", 2), ("vector database", 3)],
    "rag": [("llm/ai", 3), ("python", 3), ("vector database", 2)],
    # ── BI tools → foundation ──────────────────────────────────────────
    "tableau": [("sql", 2)],
    "power bi": [("sql", 2)],
    "looker": [("sql", 3)],
    "qlik": [("sql", 2)],
    "metabase": [("sql", 2)],
    # ── Cybersecurity → foundation ─────────────────────────────────────
    "splunk": [("linux", 2), ("siem", 3)],
    "burp suite": [("security", 3), ("penetration testing", 3)],
    "metasploit": [("security", 3), ("penetration testing", 3), ("linux", 2)],
    "kali linux": [("linux", 3), ("security", 2), ("penetration testing", 2)],
    # ── Enterprise / CRM → foundation ──────────────────────────────────
    "salesforce": [("sql", 2), ("rest api", 2)],
    # ── Networking → foundation ────────────────────────────────────────
    "cisco": [("networking", 3), ("linux", 2)],
    "load balancing": [("networking", 2), ("linux", 2)],
    "dns": [("networking", 2)],
    # ── Low-code → foundation ──────────────────────────────────────────
    "power apps": [("microsoft dynamics", 2)],
    "power automate": [("microsoft office", 2)],
    "retool": [("sql", 2), ("javascript", 2)],
    # ── Messaging → foundation ─────────────────────────────────────────
    "grpc": [("rest api", 2)],
    # ── Methodology adjacency ──────────────────────────────────────────
    "scrum": [("agile", 3), ("jira", 2)],
    "kanban": [("agile", 3)],
    "safe": [("agile", 3), ("scrum", 2)],
}


def _normalize_skill(name: str) -> str:
    """Normalize a skill name for matching."""
    n = name.lower().strip().rstrip(".")
    return _SKILL_ALIASES.get(n, n)


# ── Skill Transferability ──────────────────────────────────────────────
# When a job needs skill A but the candidate has skill B, this map defines
# how transferable B is to A. Score 0.0-1.0 (1.0 = identical, 0.7 = highly transferable).
# Bidirectional: if react→vue is 0.7, vue→react is also 0.7.
_SKILL_TRANSFERABILITY = {
    # Frontend frameworks (high transferability)
    ("react", "vue"): 0.70,
    ("react", "angular"): 0.60,
    ("react", "svelte"): 0.65,
    ("vue", "angular"): 0.60,
    ("vue", "svelte"): 0.65,
    ("angular", "svelte"): 0.55,
    ("next.js", "nuxt.js"): 0.70,
    ("next.js", "remix"): 0.75,
    ("next.js", "gatsby"): 0.65,
    # Backend languages (moderate transferability)
    ("python", "ruby"): 0.55,
    ("python", "javascript"): 0.45,
    ("python", "go"): 0.40,
    ("java", "c#"): 0.70,
    ("java", "kotlin"): 0.75,
    ("java", "scala"): 0.60,
    ("c#", "java"): 0.70,
    ("kotlin", "java"): 0.75,
    ("ruby", "python"): 0.55,
    ("go", "rust"): 0.45,
    ("typescript", "javascript"): 0.90,
    ("javascript", "typescript"): 0.80,
    # Backend frameworks (same language = higher transfer)
    ("django", "flask"): 0.75,
    ("django", "fastapi"): 0.70,
    ("flask", "fastapi"): 0.75,
    ("express", "nestjs"): 0.65,
    ("express", "fastapi"): 0.40,
    ("spring boot", ".net"): 0.50,
    ("laravel", "symfony"): 0.70,
    ("rails", "django"): 0.50,
    # Databases (high transferability within type)
    ("postgresql", "mysql"): 0.85,
    ("postgresql", "oracle"): 0.70,
    ("mysql", "oracle"): 0.70,
    ("mysql", "postgresql"): 0.85,
    ("mongodb", "couchbase"): 0.65,
    ("mongodb", "dynamodb"): 0.55,
    ("mongodb", "firestore"): 0.55,
    ("redis", "memcached"): 0.70,
    # Cloud (moderate transferability)
    ("aws", "gcp"): 0.60,
    ("aws", "azure"): 0.60,
    ("gcp", "azure"): 0.60,
    # Data platforms
    ("snowflake", "bigquery"): 0.75,
    ("snowflake", "redshift"): 0.75,
    ("bigquery", "redshift"): 0.70,
    ("databricks", "spark"): 0.80,
    # Mobile
    ("react native", "flutter"): 0.55,
    ("ios", "android"): 0.45,
    ("swift", "kotlin"): 0.40,
    # CI/CD tools (high transferability)
    ("ci/cd", "docker"): 0.40,
    # BI tools
    ("tableau", "power bi"): 0.75,
    ("tableau", "looker"): 0.65,
    ("power bi", "looker"): 0.65,
    # ML frameworks
    ("tensorflow", "pytorch"): 0.70,
    ("pytorch", "tensorflow"): 0.70,
    ("scikit-learn", "tensorflow"): 0.45,
    # CRM / ERP
    ("salesforce", "hubspot"): 0.50,
    ("salesforce", "microsoft dynamics"): 0.45,
    # Testing frameworks
    ("jest", "vitest"): 0.85,
    ("jest", "mocha"): 0.70,
    ("cypress", "playwright"): 0.80,
    ("selenium", "playwright"): 0.65,
    ("selenium", "cypress"): 0.65,
    ("pytest", "junit"): 0.50,
    # Methodology
    ("scrum", "kanban"): 0.70,
    ("scrum", "safe"): 0.65,
    ("agile", "scrum"): 0.85,
    ("agile", "kanban"): 0.80,
    # Vector DBs
    ("pinecone", "chroma"): 0.80,
    ("pinecone", "weaviate"): 0.75,
    ("pinecone", "milvus"): 0.75,
    ("chroma", "weaviate"): 0.75,

    # ═══════════════════════════════════════════════════════════════════
    # Business / Strategy / Operations skill transferability
    # ═══════════════════════════════════════════════════════════════════

    # Client/Customer Experience (high transferability within CX domain)
    ("client experience strategy", "customer experience"): 0.85,
    ("client experience strategy", "customer success"): 0.65,
    ("client experience strategy", "service design"): 0.60,
    ("customer experience", "customer success"): 0.70,
    ("customer experience", "user experience"): 0.55,
    ("customer success", "customer service"): 0.60,

    # Account & Business Development (high internal transferability)
    ("account management", "key account management"): 0.90,
    ("account management", "client management"): 0.80,
    ("account management", "relationship management"): 0.70,
    ("account management", "customer success"): 0.60,
    ("business development", "sales strategy"): 0.75,
    ("business development", "partnership development"): 0.65,
    ("business development", "account management"): 0.55,
    ("sales strategy", "go-to-market strategy"): 0.70,
    ("partnership development", "vendor management"): 0.55,

    # Strategy & Planning (moderate cross-transferability)
    ("strategic planning", "business strategy"): 0.85,
    ("strategic planning", "go-to-market strategy"): 0.65,
    ("strategic planning", "workforce planning"): 0.50,
    ("business strategy", "go-to-market strategy"): 0.70,
    ("business strategy", "digital transformation"): 0.50,

    # Operations & Process (high within ops domain)
    ("operational excellence", "process improvement"): 0.80,
    ("operational excellence", "six sigma"): 0.65,
    ("operational excellence", "lean"): 0.65,
    ("process improvement", "six sigma"): 0.75,
    ("process improvement", "lean"): 0.75,
    ("change management", "organizational development"): 0.60,
    ("change management", "transformation management"): 0.80,
    ("governance", "compliance"): 0.65,
    ("governance", "risk management"): 0.60,

    # Experience Design & Innovation
    ("experience design", "service design"): 0.80,
    ("experience design", "user experience"): 0.65,
    ("experience design", "design thinking"): 0.70,
    ("service design", "design thinking"): 0.75,
    ("innovation", "design thinking"): 0.55,

    # Leadership & Team Management
    ("team leadership", "people management"): 0.85,
    ("team leadership", "people development"): 0.75,
    ("people management", "people development"): 0.80,
    ("people management", "talent management"): 0.65,
    ("talent management", "talent acquisition"): 0.55,

    # Stakeholder Engagement
    ("stakeholder engagement", "stakeholder management"): 0.90,
    ("stakeholder engagement", "executive communication"): 0.60,
    ("stakeholder management", "client management"): 0.65,
    ("stakeholder management", "relationship management"): 0.75,
    ("executive communication", "executive storytelling"): 0.80,

    # Program & Project Management
    ("program management", "project management"): 0.80,
    ("program management", "portfolio management"): 0.70,
    ("project management", "delivery management"): 0.65,
    ("delivery management", "release management"): 0.55,

    # Marketing & Brand
    ("marketing strategy", "brand strategy"): 0.70,
    ("marketing strategy", "digital marketing"): 0.60,
    ("brand strategy", "brand management"): 0.85,
    ("content strategy", "content marketing"): 0.80,
    ("digital marketing", "content marketing"): 0.55,

    # Finance & Analytics
    ("financial analysis", "business analysis"): 0.60,
    ("financial analysis", "data analysis"): 0.55,
    ("business analysis", "data analysis"): 0.65,
    ("business analysis", "requirements analysis"): 0.70,

    # HR & People
    ("talent acquisition", "recruitment"): 0.90,
    ("talent acquisition", "employer branding"): 0.50,
    ("performance management", "people development"): 0.65,
    ("compensation & benefits", "total rewards"): 0.85,

    # Cross-domain: Business ↔ Tech
    ("product management", "project management"): 0.50,
    ("product management", "business analysis"): 0.55,
    ("data analysis", "sql"): 0.40,
    ("business intelligence", "data analysis"): 0.70,
    ("business intelligence", "tableau"): 0.50,
    ("business intelligence", "power bi"): 0.50,
}


def _get_transferability(skill_a: str, skill_b: str) -> float:
    """Get transferability score between two skills (0.0 if not transferable)."""
    a = _normalize_skill(skill_a)
    b = _normalize_skill(skill_b)
    return _SKILL_TRANSFERABILITY.get((a, b), _SKILL_TRANSFERABILITY.get((b, a), 0.0))


# ── Job Role → Expected Skill Stacks ──────────────────────────────────
# Maps common job titles to their expected core skills.
# Used by job parsing to auto-suggest skills when a recruiter creates a job
# with just a title and no detailed skill requirements.
_ROLE_SKILL_STACKS = {
    "frontend developer": [
        {"skill": "HTML", "min_depth": 3, "weight": 0.9, "category": "language"},
        {"skill": "CSS", "min_depth": 3, "weight": 0.9, "category": "language"},
        {"skill": "JavaScript", "min_depth": 3, "weight": 1.0, "category": "language"},
        {"skill": "React", "min_depth": 3, "weight": 0.8, "category": "framework"},
        {"skill": "TypeScript", "min_depth": 2, "weight": 0.7, "category": "language"},
        {"skill": "Git", "min_depth": 2, "weight": 0.6, "category": "tool"},
        {"skill": "Responsive Design", "min_depth": 2, "weight": 0.7, "category": "concept"},
    ],
    "backend developer": [
        {"skill": "Python", "min_depth": 3, "weight": 0.9, "category": "language"},
        {"skill": "SQL", "min_depth": 3, "weight": 0.9, "category": "language"},
        {"skill": "REST API", "min_depth": 3, "weight": 0.9, "category": "concept"},
        {"skill": "Git", "min_depth": 2, "weight": 0.6, "category": "tool"},
        {"skill": "Docker", "min_depth": 2, "weight": 0.6, "category": "devops"},
        {"skill": "PostgreSQL", "min_depth": 2, "weight": 0.7, "category": "database"},
    ],
    "full stack developer": [
        {"skill": "HTML", "min_depth": 3, "weight": 0.8, "category": "language"},
        {"skill": "CSS", "min_depth": 3, "weight": 0.8, "category": "language"},
        {"skill": "JavaScript", "min_depth": 3, "weight": 1.0, "category": "language"},
        {"skill": "React", "min_depth": 3, "weight": 0.8, "category": "framework"},
        {"skill": "Node.js", "min_depth": 3, "weight": 0.8, "category": "framework"},
        {"skill": "SQL", "min_depth": 3, "weight": 0.8, "category": "language"},
        {"skill": "REST API", "min_depth": 3, "weight": 0.8, "category": "concept"},
        {"skill": "Git", "min_depth": 2, "weight": 0.6, "category": "tool"},
    ],
    "data engineer": [
        {"skill": "SQL", "min_depth": 4, "weight": 1.0, "category": "language"},
        {"skill": "Python", "min_depth": 3, "weight": 0.9, "category": "language"},
        {"skill": "Spark", "min_depth": 3, "weight": 0.8, "category": "data"},
        {"skill": "ETL/ELT", "min_depth": 3, "weight": 0.9, "category": "concept"},
        {"skill": "Airflow", "min_depth": 2, "weight": 0.7, "category": "data"},
        {"skill": "Docker", "min_depth": 2, "weight": 0.5, "category": "devops"},
    ],
    "data scientist": [
        {"skill": "Python", "min_depth": 4, "weight": 1.0, "category": "language"},
        {"skill": "SQL", "min_depth": 3, "weight": 0.8, "category": "language"},
        {"skill": "Machine Learning", "min_depth": 3, "weight": 1.0, "category": "ai"},
        {"skill": "Pandas", "min_depth": 3, "weight": 0.8, "category": "data"},
        {"skill": "Scikit-learn", "min_depth": 3, "weight": 0.7, "category": "ai"},
        {"skill": "Jupyter", "min_depth": 2, "weight": 0.5, "category": "tool"},
    ],
    "data analyst": [
        {"skill": "SQL", "min_depth": 3, "weight": 1.0, "category": "language"},
        {"skill": "Python", "min_depth": 2, "weight": 0.7, "category": "language"},
        {"skill": "Tableau", "min_depth": 2, "weight": 0.7, "category": "tool"},
        {"skill": "Excel", "min_depth": 3, "weight": 0.7, "category": "general_tool"},
        {"skill": "Data Science", "min_depth": 2, "weight": 0.6, "category": "data"},
    ],
    "machine learning engineer": [
        {"skill": "Python", "min_depth": 4, "weight": 1.0, "category": "language"},
        {"skill": "Machine Learning", "min_depth": 4, "weight": 1.0, "category": "ai"},
        {"skill": "TensorFlow", "min_depth": 3, "weight": 0.7, "category": "ai"},
        {"skill": "PyTorch", "min_depth": 3, "weight": 0.7, "category": "ai"},
        {"skill": "SQL", "min_depth": 2, "weight": 0.6, "category": "language"},
        {"skill": "Docker", "min_depth": 2, "weight": 0.6, "category": "devops"},
    ],
    "ai engineer": [
        {"skill": "Python", "min_depth": 4, "weight": 1.0, "category": "language"},
        {"skill": "LLM/AI", "min_depth": 3, "weight": 1.0, "category": "ai"},
        {"skill": "Machine Learning", "min_depth": 3, "weight": 0.8, "category": "ai"},
        {"skill": "LangChain", "min_depth": 2, "weight": 0.6, "category": "ai"},
        {"skill": "REST API", "min_depth": 2, "weight": 0.6, "category": "concept"},
        {"skill": "Docker", "min_depth": 2, "weight": 0.5, "category": "devops"},
    ],
    "devops engineer": [
        {"skill": "Linux", "min_depth": 4, "weight": 1.0, "category": "devops"},
        {"skill": "Docker", "min_depth": 3, "weight": 0.9, "category": "devops"},
        {"skill": "Kubernetes", "min_depth": 3, "weight": 0.8, "category": "devops"},
        {"skill": "CI/CD", "min_depth": 3, "weight": 0.9, "category": "devops"},
        {"skill": "Terraform", "min_depth": 2, "weight": 0.7, "category": "devops"},
        {"skill": "AWS", "min_depth": 2, "weight": 0.7, "category": "cloud"},
        {"skill": "Python", "min_depth": 2, "weight": 0.5, "category": "language"},
    ],
    "site reliability engineer": [
        {"skill": "Linux", "min_depth": 4, "weight": 1.0, "category": "devops"},
        {"skill": "Docker", "min_depth": 3, "weight": 0.8, "category": "devops"},
        {"skill": "Kubernetes", "min_depth": 3, "weight": 0.9, "category": "devops"},
        {"skill": "CI/CD", "min_depth": 3, "weight": 0.8, "category": "devops"},
        {"skill": "Python", "min_depth": 3, "weight": 0.7, "category": "language"},
        {"skill": "Prometheus", "min_depth": 2, "weight": 0.6, "category": "devops"},
        {"skill": "Grafana", "min_depth": 2, "weight": 0.5, "category": "devops"},
    ],
    "platform engineer": [
        {"skill": "Linux", "min_depth": 4, "weight": 1.0, "category": "devops"},
        {"skill": "Kubernetes", "min_depth": 4, "weight": 1.0, "category": "devops"},
        {"skill": "Docker", "min_depth": 3, "weight": 0.9, "category": "devops"},
        {"skill": "CI/CD", "min_depth": 3, "weight": 0.8, "category": "devops"},
        {"skill": "Terraform", "min_depth": 3, "weight": 0.8, "category": "devops"},
        {"skill": "Python", "min_depth": 2, "weight": 0.6, "category": "language"},
    ],
    "cloud architect": [
        {"skill": "AWS", "min_depth": 4, "weight": 1.0, "category": "cloud"},
        {"skill": "Docker", "min_depth": 3, "weight": 0.7, "category": "devops"},
        {"skill": "Kubernetes", "min_depth": 3, "weight": 0.7, "category": "devops"},
        {"skill": "Terraform", "min_depth": 3, "weight": 0.8, "category": "devops"},
        {"skill": "System Design", "min_depth": 4, "weight": 0.9, "category": "concept"},
        {"skill": "Security", "min_depth": 3, "weight": 0.7, "category": "concept"},
        {"skill": "Linux", "min_depth": 3, "weight": 0.6, "category": "devops"},
    ],
    "solutions architect": [
        {"skill": "System Design", "min_depth": 4, "weight": 1.0, "category": "concept"},
        {"skill": "REST API", "min_depth": 3, "weight": 0.8, "category": "concept"},
        {"skill": "AWS", "min_depth": 3, "weight": 0.7, "category": "cloud"},
        {"skill": "SQL", "min_depth": 3, "weight": 0.6, "category": "language"},
        {"skill": "Security", "min_depth": 2, "weight": 0.6, "category": "concept"},
        {"skill": "Docker", "min_depth": 2, "weight": 0.5, "category": "devops"},
    ],
    "security engineer": [
        {"skill": "Security", "min_depth": 4, "weight": 1.0, "category": "concept"},
        {"skill": "Linux", "min_depth": 3, "weight": 0.8, "category": "devops"},
        {"skill": "Networking", "min_depth": 3, "weight": 0.7, "category": "concept"},
        {"skill": "Python", "min_depth": 2, "weight": 0.6, "category": "language"},
        {"skill": "Penetration Testing", "min_depth": 2, "weight": 0.6, "category": "concept"},
        {"skill": "SIEM", "min_depth": 2, "weight": 0.5, "category": "tool"},
    ],
    "qa engineer": [
        {"skill": "Unit Testing", "min_depth": 3, "weight": 0.9, "category": "testing"},
        {"skill": "Integration Testing", "min_depth": 3, "weight": 0.8, "category": "testing"},
        {"skill": "Selenium", "min_depth": 2, "weight": 0.7, "category": "testing"},
        {"skill": "Python", "min_depth": 2, "weight": 0.6, "category": "language"},
        {"skill": "SQL", "min_depth": 2, "weight": 0.5, "category": "language"},
        {"skill": "CI/CD", "min_depth": 2, "weight": 0.5, "category": "devops"},
    ],
    "mobile developer": [
        {"skill": "React Native", "min_depth": 3, "weight": 0.8, "category": "mobile"},
        {"skill": "JavaScript", "min_depth": 3, "weight": 0.8, "category": "language"},
        {"skill": "REST API", "min_depth": 3, "weight": 0.8, "category": "concept"},
        {"skill": "Git", "min_depth": 2, "weight": 0.5, "category": "tool"},
    ],
    "android developer": [
        {"skill": "Kotlin", "min_depth": 3, "weight": 1.0, "category": "language"},
        {"skill": "Android", "min_depth": 3, "weight": 1.0, "category": "mobile"},
        {"skill": "Java", "min_depth": 2, "weight": 0.6, "category": "language"},
        {"skill": "REST API", "min_depth": 3, "weight": 0.7, "category": "concept"},
        {"skill": "SQL", "min_depth": 2, "weight": 0.5, "category": "language"},
    ],
    "ios developer": [
        {"skill": "Swift", "min_depth": 3, "weight": 1.0, "category": "language"},
        {"skill": "iOS", "min_depth": 3, "weight": 1.0, "category": "mobile"},
        {"skill": "REST API", "min_depth": 3, "weight": 0.7, "category": "concept"},
        {"skill": "Git", "min_depth": 2, "weight": 0.5, "category": "tool"},
    ],
    "product manager": [
        {"skill": "Jira", "min_depth": 3, "weight": 0.8, "category": "tool"},
        {"skill": "Agile", "min_depth": 3, "weight": 0.9, "category": "concept"},
        {"skill": "SQL", "min_depth": 2, "weight": 0.5, "category": "language"},
    ],
    "technical writer": [
        {"skill": "Technical Writing", "min_depth": 4, "weight": 1.0, "category": "tool"},
        {"skill": "Markdown", "min_depth": 3, "weight": 0.7, "category": "tool"},
        {"skill": "Git", "min_depth": 2, "weight": 0.5, "category": "tool"},
    ],
    "blockchain developer": [
        {"skill": "Solidity", "min_depth": 3, "weight": 1.0, "category": "language"},
        {"skill": "Ethereum", "min_depth": 3, "weight": 0.9, "category": "concept"},
        {"skill": "Web3", "min_depth": 3, "weight": 0.8, "category": "framework"},
        {"skill": "JavaScript", "min_depth": 3, "weight": 0.7, "category": "language"},
        {"skill": "Smart Contracts", "min_depth": 3, "weight": 0.9, "category": "concept"},
    ],
    "database administrator": [
        {"skill": "SQL", "min_depth": 5, "weight": 1.0, "category": "language"},
        {"skill": "PostgreSQL", "min_depth": 4, "weight": 0.8, "category": "database"},
        {"skill": "Linux", "min_depth": 3, "weight": 0.6, "category": "devops"},
        {"skill": "Python", "min_depth": 2, "weight": 0.4, "category": "language"},
    ],
    "network engineer": [
        {"skill": "Networking", "min_depth": 4, "weight": 1.0, "category": "concept"},
        {"skill": "Cisco", "min_depth": 3, "weight": 0.8, "category": "tool"},
        {"skill": "Linux", "min_depth": 3, "weight": 0.7, "category": "devops"},
        {"skill": "DNS", "min_depth": 3, "weight": 0.6, "category": "concept"},
        {"skill": "VPN", "min_depth": 2, "weight": 0.5, "category": "concept"},
        {"skill": "Load Balancing", "min_depth": 2, "weight": 0.5, "category": "concept"},
    ],
    "salesforce developer": [
        {"skill": "Salesforce", "min_depth": 4, "weight": 1.0, "category": "tool"},
        {"skill": "SQL", "min_depth": 2, "weight": 0.6, "category": "language"},
        {"skill": "JavaScript", "min_depth": 2, "weight": 0.5, "category": "language"},
        {"skill": "REST API", "min_depth": 2, "weight": 0.5, "category": "concept"},
    ],
    "sap consultant": [
        {"skill": "SAP", "min_depth": 4, "weight": 1.0, "category": "tool"},
        {"skill": "SQL", "min_depth": 2, "weight": 0.5, "category": "language"},
    ],
    "ux designer": [
        {"skill": "Figma", "min_depth": 4, "weight": 1.0, "category": "tool"},
        {"skill": "HTML", "min_depth": 2, "weight": 0.5, "category": "language"},
        {"skill": "CSS", "min_depth": 2, "weight": 0.5, "category": "language"},
        {"skill": "Responsive Design", "min_depth": 3, "weight": 0.8, "category": "concept"},
        {"skill": "Accessibility", "min_depth": 2, "weight": 0.6, "category": "concept"},
    ],
    "ui developer": [
        {"skill": "HTML", "min_depth": 4, "weight": 1.0, "category": "language"},
        {"skill": "CSS", "min_depth": 4, "weight": 1.0, "category": "language"},
        {"skill": "JavaScript", "min_depth": 3, "weight": 0.9, "category": "language"},
        {"skill": "Figma", "min_depth": 2, "weight": 0.6, "category": "tool"},
        {"skill": "Responsive Design", "min_depth": 3, "weight": 0.8, "category": "concept"},
        {"skill": "Accessibility", "min_depth": 2, "weight": 0.7, "category": "concept"},
    ],
    "wordpress developer": [
        {"skill": "WordPress", "min_depth": 4, "weight": 1.0, "category": "framework"},
        {"skill": "PHP", "min_depth": 3, "weight": 0.8, "category": "language"},
        {"skill": "HTML", "min_depth": 3, "weight": 0.7, "category": "language"},
        {"skill": "CSS", "min_depth": 3, "weight": 0.7, "category": "language"},
        {"skill": "JavaScript", "min_depth": 2, "weight": 0.6, "category": "language"},
        {"skill": "MySQL", "min_depth": 2, "weight": 0.5, "category": "database"},
    ],
    "embedded engineer": [
        {"skill": "C", "min_depth": 4, "weight": 1.0, "category": "language"},
        {"skill": "C++", "min_depth": 3, "weight": 0.8, "category": "language"},
        {"skill": "Linux", "min_depth": 3, "weight": 0.7, "category": "devops"},
        {"skill": "Python", "min_depth": 2, "weight": 0.4, "category": "language"},
    ],
}


# Build a lookup with normalized keys and common variations
_ROLE_ALIASES = {}
for role_key in _ROLE_SKILL_STACKS:
    _ROLE_ALIASES[role_key] = role_key
    # Generate common variations: "senior X", "lead X", "junior X", "sr. X", "X engineer" etc.
    _ROLE_ALIASES[f"senior {role_key}"] = role_key
    _ROLE_ALIASES[f"lead {role_key}"] = role_key
    _ROLE_ALIASES[f"junior {role_key}"] = role_key
    _ROLE_ALIASES[f"sr. {role_key}"] = role_key
    _ROLE_ALIASES[f"sr {role_key}"] = role_key
    _ROLE_ALIASES[f"principal {role_key}"] = role_key
    _ROLE_ALIASES[f"staff {role_key}"] = role_key


def get_role_skill_stack(title: str) -> list:
    """Look up expected skill stack for a job title. Returns empty list if not found."""
    if not title:
        return []
    title_lower = title.lower().strip()
    # Direct match
    role_key = _ROLE_ALIASES.get(title_lower)
    if role_key:
        return _ROLE_SKILL_STACKS[role_key]
    # Fuzzy: check if any role key is contained in the title
    for key in _ROLE_SKILL_STACKS:
        if key in title_lower:
            return _ROLE_SKILL_STACKS[key]
    return []


# ── Certification → Skill Boost Mapping ────────────────────────────────
# When a candidate has a certification, the specified skills get a depth boost.
# Format: cert_keyword → [(skill, depth_boost)]
_CERTIFICATION_BOOSTS = {
    "aws solutions architect": [("aws", 2), ("system design", 1), ("security", 1)],
    "aws developer": [("aws", 2), ("lambda", 1), ("rest api", 1)],
    "aws sysops": [("aws", 2), ("linux", 1)],
    "aws devops": [("aws", 2), ("ci/cd", 1), ("docker", 1)],
    "aws cloud practitioner": [("aws", 1)],
    "azure administrator": [("azure", 2), ("linux", 1)],
    "azure developer": [("azure", 2)],
    "azure solutions architect": [("azure", 2), ("system design", 1)],
    "google cloud professional": [("gcp", 2)],
    "google cloud architect": [("gcp", 2), ("system design", 1)],
    "google cloud engineer": [("gcp", 2), ("linux", 1)],
    "cka": [("kubernetes", 2), ("docker", 1), ("linux", 1)],
    "ckad": [("kubernetes", 2), ("docker", 1)],
    "certified kubernetes": [("kubernetes", 2), ("docker", 1)],
    "docker certified": [("docker", 2), ("linux", 1)],
    "terraform associate": [("terraform", 2), ("linux", 1)],
    "hashicorp certified": [("terraform", 2)],
    "pmp": [("agile", 1), ("jira", 1)],
    "csm": [("scrum", 2), ("agile", 1)],
    "certified scrum master": [("scrum", 2), ("agile", 1)],
    "safe agilist": [("safe", 2), ("agile", 1)],
    "cisco ccna": [("cisco", 2), ("networking", 2)],
    "cisco ccnp": [("cisco", 3), ("networking", 3)],
    "ccna": [("cisco", 2), ("networking", 2)],
    "ccnp": [("cisco", 3), ("networking", 3)],
    "comptia security+": [("security", 2)],
    "comptia network+": [("networking", 2)],
    "oscp": [("penetration testing", 3), ("security", 2), ("linux", 2)],
    "cissp": [("security", 3)],
    "ceh": [("security", 2), ("penetration testing", 2)],
    "salesforce certified": [("salesforce", 2)],
    "salesforce administrator": [("salesforce", 2)],
    "salesforce developer": [("salesforce", 2), ("rest api", 1)],
    "sap certified": [("sap", 2)],
    "oracle certified": [("oracle", 2), ("sql", 1)],
    "microsoft certified": [("microsoft office", 1)],
    "az-900": [("azure", 1)],
    "az-104": [("azure", 2)],
    "az-204": [("azure", 2)],
    "az-305": [("azure", 2), ("system design", 1)],
    "dp-900": [("sql", 1)],
    "dp-203": [("azure", 2), ("sql", 2), ("spark", 1)],
    "ml engineer": [("machine learning", 2), ("python", 1)],
    "tensorflow developer": [("tensorflow", 2), ("python", 1)],
    "databricks certified": [("databricks", 2), ("spark", 1), ("python", 1)],
    "snowflake": [("snowflake", 2), ("sql", 1)],
    "dbt certification": [("dbt", 2), ("sql", 1)],
}


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


def _apply_adjacency_boosts(assessments, parsed_resume: dict = None):
    """
    Apply the skill adjacency graph AND umbrella skill expansion to boost implied skills.

    Two-phase boosting:
    1. Framework adjacency: React at depth 4 → HTML/CSS/JS floored at 3.
    2. Umbrella expansion: If resume mentions "web development" or "full-stack",
       constituent skills (HTML, CSS, JS, etc.) get boosted based on the umbrella context.

    This is applied post-LLM to catch cases the LLM missed.
    """
    skill_map = {}
    for a in assessments:
        skill_map[_normalize_skill(a.name)] = a

    # Collect all boost targets
    boosts = {}  # canonical_name → (max_implied_depth, reason)

    # ── Phase 1: Framework → foundation adjacency ─────────────────────
    for a in assessments:
        if a.estimated_depth >= 3:
            canonical = _normalize_skill(a.name)
            adjacencies = _SKILL_ADJACENCY.get(canonical, [])
            for related_skill, implied_depth in adjacencies:
                current = boosts.get(related_skill, (0, ""))
                if implied_depth > current[0]:
                    boosts[related_skill] = (implied_depth, f"adjacent to {a.name}")

    # ── Phase 2: Umbrella skill expansion from resume context ─────────
    # Scan resume text for umbrella terms and boost their constituent skills
    if parsed_resume:
        _scan_text = ""
        for exp in (parsed_resume.get("experience") or []):
            _scan_text += " " + (exp.get("description") or "")
            _scan_text += " " + (exp.get("title") or "")
        for proj in (parsed_resume.get("projects") or []):
            _scan_text += " " + (proj.get("description") or "")
        _scan_text += " " + (parsed_resume.get("summary") or "")
        _scan_text += " " + " ".join(parsed_resume.get("skills_mentioned") or [])
        _scan_text = _scan_text.lower()

        for umbrella_term, constituents in _UMBRELLA_SKILLS.items():
            if umbrella_term in _scan_text:
                for skill_name, implied_depth in constituents:
                    current = boosts.get(skill_name, (0, ""))
                    if implied_depth > current[0]:
                        boosts[skill_name] = (implied_depth, f"implied by '{umbrella_term}' on resume")

    # ── Phase 3: Certification → skill boosts ─────────────────────────
    if parsed_resume:
        certs = parsed_resume.get("certifications") or []
        for cert in certs:
            cert_lower = (cert if isinstance(cert, str) else str(cert)).lower()
            for cert_key, skill_boosts in _CERTIFICATION_BOOSTS.items():
                if cert_key in cert_lower:
                    for skill_name, depth_boost in skill_boosts:
                        current = boosts.get(skill_name, (0, ""))
                        implied = min(depth_boost + 1, 4)  # cert boost + 1 baseline, cap at 4
                        if implied > current[0]:
                            boosts[skill_name] = (implied, f"certification: {cert_key}")

    # ── Phase 4: Skill transferability credit ─────────────────────────
    # If a candidate has React but the job needs Vue, apply partial credit
    for a in assessments:
        if a.estimated_depth >= 3:
            a_canonical = _normalize_skill(a.name)
            for target_a in assessments:
                target_canonical = _normalize_skill(target_a.name)
                if target_canonical != a_canonical and target_a.estimated_depth < 2:
                    transfer = _get_transferability(a_canonical, target_canonical)
                    if transfer >= 0.4:
                        # Transferable skill: give partial depth credit
                        implied = max(2, int(a.estimated_depth * transfer * 0.8))
                        implied = min(implied, a.estimated_depth - 1)  # Never exceed source
                        current = boosts.get(target_canonical, (0, ""))
                        if implied > current[0]:
                            boosts[target_canonical] = (
                                implied,
                                f"transferable from {a.name} ({transfer:.0%} transferability)"
                            )

    # ── Apply boosts ──────────────────────────────────────────────────
    for a in assessments:
        canonical = _normalize_skill(a.name)
        if canonical in boosts:
            implied_depth, reason = boosts[canonical]
            if a.estimated_depth < implied_depth:
                old_depth = a.estimated_depth
                a.estimated_depth = implied_depth
                a.depth_confidence = max(a.depth_confidence, 0.65)
                a.depth_reasoning = (
                    f"Boosted from {old_depth} to {implied_depth} ({reason}). "
                    + a.depth_reasoning
                )
                logger.info(f"Adjacency boost: {a.name} {old_depth} -> {implied_depth} ({reason})")


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
    for exp in parsed_resume.get("experience") or []:
        desc = exp.get("description") or ""
        company = exp.get("company") or ""
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
    Compute a recency factor for a skill based on when it was last used.
    More recent usage = higher factor. Skills from >5 years ago get penalized.

    Returns:
    - 1.0 if used within 2 years
    - 0.90 if used 3-4 years ago
    - 0.75 if used 5-7 years ago
    - 0.55 if used 8+ years ago

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

    # Smooth recency decay curve (no hard cliffs):
    #   0 years ago → 1.0 (current)
    #   2 years ago → 1.0 (grace period)
    #   4 years ago → ~0.85
    #   6 years ago → ~0.70
    #   8 years ago → ~0.55
    #   10+ years ago → 0.50 (floor)
    if years_ago <= 2:
        return 1.0
    # Smooth exponential-ish decay: max(0.50, 1.0 - 0.075 * (years_ago - 2))
    return max(0.50, round(1.0 - 0.075 * (years_ago - 2), 3))


def _estimate_years_since_last_use(skill_name: str, parsed_resume: dict, current_year: int) -> int:
    """Estimate how many years ago a skill was last used by scanning experience entries.

    Checks multiple signals per experience entry:
    1. Technologies list (exact or substring match)
    2. Description text (substring match)
    3. Role title (for domain/business skills like 'Account Management')
    4. Keyword variants (e.g., 'account' matches 'Account Management' skill)
    """
    skill_lower = skill_name.lower()
    most_recent_year = 0

    # Build keyword variants for broader matching
    # Split multi-word skill names into component keywords
    skill_words = set(skill_lower.split())
    # Remove very common words that would cause false positives
    _STOP_WORDS = {"and", "or", "the", "a", "an", "of", "in", "for", "to", "with", "on", "at", "by"}
    skill_keywords = skill_words - _STOP_WORDS

    # Add basic stems for common morphological variants
    # e.g., "management" also matches "manager", "managing", "managed"
    # Use a simple stem map for common word roots rather than suffix stripping
    _STEM_GROUPS = {
        "manage": {"management", "manager", "managing", "managed", "manages"},
        "develop": {"development", "developer", "developing", "developed", "develops"},
        "account": {"account", "accounting", "accounts", "accountant"},
        "project": {"project", "projects"},
        "operate": {"operation", "operations", "operational", "operating", "operates"},
        "design": {"design", "designer", "designing", "designed", "designs"},
        "strateg": {"strategy", "strategic", "strategies", "strategist"},
        "lead": {"lead", "leader", "leading", "leads", "leadership"},
        "business": {"business", "businesses"},
        "client": {"client", "clients"},
        "experience": {"experience", "experienced", "experiences"},
        "deliver": {"delivery", "delivering", "delivered", "delivers", "deliverable"},
        "govern": {"governance", "governing", "governed", "governs"},
        "excel": {"excellence", "excellent"},
        "innovat": {"innovation", "innovative", "innovating", "innovate", "innovates"},
        "consult": {"consulting", "consultant", "consultancy", "consulted"},
        "market": {"marketing", "market", "markets", "marketer"},
        "sales": {"sales", "sale", "selling"},
        "plan": {"planning", "planned", "plans", "planner"},
        "engag": {"engagement", "engaging", "engaged", "engages"},
        "stakeholder": {"stakeholder", "stakeholders"},
        "partner": {"partner", "partnership", "partners", "partnered"},
    }
    # Build a word → group lookup
    _word_to_group = {}
    for root, variants in _STEM_GROUPS.items():
        for v in variants:
            _word_to_group[v] = variants

    skill_keywords_expanded = set()
    for kw in skill_keywords:
        skill_keywords_expanded.add(kw)
        if kw in _word_to_group:
            skill_keywords_expanded.update(_word_to_group[kw])

    for exp in parsed_resume.get("experience") or []:
        # Check technologies list
        techs = [t.lower() for t in (exp.get("technologies") or [])]
        desc_lower = (exp.get("description") or "").lower()
        title_lower = (exp.get("title") or "").lower()

        matched = False

        # Signal 1: Exact or substring match in technologies
        if skill_lower in techs or any(skill_lower in t or t in skill_lower for t in techs):
            matched = True

        # Signal 2: Skill name found in description
        if not matched and skill_lower in desc_lower:
            matched = True

        # Signal 3: Skill name found in role title (important for business skills)
        if not matched and skill_lower in title_lower:
            matched = True

        # Signal 4: Key skill words found in title and/or description (with morphological variants)
        # (e.g., skill "Account Management" matches title "Account Manager")
        # Require ALL keywords to match — but they can be spread across title + description
        if not matched and skill_keywords_expanded:
            title_words = set(title_lower.split())
            combined_text = title_lower + " " + desc_lower
            combined_words = set(combined_text.split())

            # Check if ALL skill keywords (or their stem variants) appear in combined title+description
            kw_matches = sum(
                1 for kw in skill_keywords
                if kw in combined_words
                or any(variant in combined_words for variant in (_word_to_group.get(kw) or set()))
                or kw in combined_text  # Also check as substring for compound words
                or any(variant in combined_text for variant in (_word_to_group.get(kw) or set()))
            )
            if kw_matches >= len(skill_keywords):
                matched = True

        if matched:
            end_date = exp.get("end_date") or ""
            if isinstance(end_date, str):
                if "present" in end_date.lower() or "current" in end_date.lower():
                    return 0  # Currently using
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
    "professional": 0.90,      # CA, CPA, CFA, ACA, ACCA — postgrad-equivalent professional qualifications
    "master": 0.85,
    "master's": 0.85,
    "msc": 0.85,
    "ms": 0.85,
    "mba": 0.85,
    "chartered": 0.85,         # "Chartered Accountant" etc. if LLM uses this phrasing
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
    education = parsed_resume.get("education") or []
    education_level = parsed_resume.get("education_level", "")
    certifications = parsed_resume.get("certifications") or []

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
        if not isinstance(edu, dict):
            continue
        degree = (edu.get("degree") or "").lower()
        for key, score in _EDUCATION_LEVELS.items():
            if key in degree:
                degree_score = max(degree_score, score)
                break
    degree_component = degree_score * 0.40

    # Component 2: Field relevance (0-0.25)
    # Covers both tech and non-tech professional fields
    relevant_fields = {
        # Tech fields
        "computer science", "software engineering", "computer engineering",
        "information technology", "data science", "mathematics",
        "electrical engineering", "information systems",
        "artificial intelligence", "machine learning",
        "distributed systems", "cybersecurity",
        # Business / Finance / Professional fields
        "finance", "accounting", "commerce", "business administration",
        "economics", "management", "chartered accountant", "law",
        "human resources", "marketing", "operations management",
        "public administration", "international business",
        "supply chain", "actuarial", "statistics", "banking",
    }
    field_score = 0.0
    for edu in education:
        if not isinstance(edu, dict):
            continue
        field = (edu.get("field") or "").lower()
        degree = (edu.get("degree") or "").lower()
        combined = f"{degree} {field}"
        for rf in relevant_fields:
            if rf in combined:
                field_score = 1.0
                break
        if field_score > 0:
            break
    # Also check certifications for professional field relevance
    if field_score == 0.0 and certifications:
        _professional_cert_names = {"ca", "aca", "fca", "cpa", "acca", "cfa", "acs",
                                     "icwa", "cma", "cisa", "cissp", "pmp", "aws",
                                     "azure", "gcp", "scrum", "six sigma"}
        for cert in certifications:
            cert_name = (cert.get("name", "") if isinstance(cert, dict) else str(cert)).lower()
            for pc in _professional_cert_names:
                if pc in cert_name:
                    field_score = 0.85  # Slightly less than formal degree but still strong
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
                         experience_range: dict = None, job_title: str = "",
                         role_type: dict = None, soft_skills: dict = None,
                         trajectory: dict = None, domain_fit: dict = None) -> list:
    """
    Deterministic risk flag generation based on analysis data.
    No LLM calls needed — purely rule-based.

    Four categories of flags:
      A. Inconsistency/red-flag detectors (inflation, gaps, staleness, seniority mismatch)
      B. Weak-profile warnings (low overall score, thin resume, massive skill gaps,
         shallow depth across the board, no transferable strengths)
      C. Universal scoring flags (soft skill gaps, trajectory concerns, industry mismatch)
      D. Domain-fit flags (industry mismatch, domain-specific gaps)
    """
    if experience_range is None:
        experience_range = {}
    if role_type is None:
        role_type = {}
    if soft_skills is None:
        soft_skills = {}
    if trajectory is None:
        trajectory = {}
    if domain_fit is None:
        domain_fit = {}
    flags = []

    # ── Seniority-awareness: determine candidate seniority for flag calibration ──
    candidate_years = _estimate_candidate_years(parsed_resume)
    is_senior_profile = (candidate_years is not None and candidate_years >= 12)

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
                "Ask about scope of past work: ownership of key initiatives, mentoring, "
                "cross-team impact, and strategic contributions."
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
                "and independent decision-making."
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
                "suggestion": _sanitize_text(f"Ask specific depth-probing questions about {a.name} to verify expertise level."),
            })

    # A4. Employment gaps (sorted chronologically, month-level precision)
    # Seniority-aware: senior profiles (12+ years) get higher gap threshold
    # and lower severity — career transitions are normal at executive level
    gap_threshold = 12 if is_senior_profile else 6  # months
    experiences = parsed_resume.get("experience") or []
    sorted_exps = _sort_experiences_by_start(experiences) if len(experiences) >= 2 else experiences
    if len(sorted_exps) >= 2:
        for i in range(len(sorted_exps) - 1):
            end_date = sorted_exps[i].get("end_date") or ""
            start_date = sorted_exps[i + 1].get("start_date") or ""
            gap = _estimate_gap_months(end_date, start_date)
            if gap is not None and gap > gap_threshold:
                # Senior profiles: gaps < 18 months are low severity (career transition)
                if is_senior_profile:
                    severity = "low" if gap < 18 else "medium"
                else:
                    severity = "low" if gap < 12 else "medium"
                flags.append({
                    "flag_type": "employment_gap",
                    "severity": severity,
                    "title": _sanitize_text(f"Employment gap of approximately {gap} months"),
                    "description": _sanitize_text(
                        f"There appears to be a gap of about {gap} months "
                        f"between {sorted_exps[i].get('company', 'previous role')} and "
                        f"{sorted_exps[i + 1].get('company', 'next role')}."
                    ),
                    "evidence": _sanitize_text(f"End date: {end_date}, Next start: {start_date}"),
                    "suggestion": "Ask about what the candidate was doing during this period.",
                })

    # A5. (Removed) Recency concerns were previously flagged here, but "last used X years ago"
    # flags are not surfaced. Recency is already handled by the recency weighting factor in
    # _compute_scores, which reduces effective depth for stale skills. Surfacing it as a
    # separate risk flag was redundant and potentially misleading for recruiters.

    # A6. Job hopping: many short tenures (< 1 year each)
    # Senior profiles: raise threshold — short tenures at exec level often reflect
    # board/advisory roles, interim positions, or M&A transitions
    short_tenure_threshold = 4 if is_senior_profile else 3
    if len(sorted_exps) >= 3:
        short_tenures = 0
        for exp in sorted_exps:
            # Skip the current (ongoing) role: its duration is still growing
            end_date_str = exp.get("end_date") or ""
            if _is_present_date(end_date_str):
                continue

            start_m, end_m = _parse_experience_dates(exp)
            if start_m is None or end_m is None:
                continue
            tenure_months = end_m - start_m
            if 0 <= tenure_months < 12:
                short_tenures += 1

        if short_tenures >= short_tenure_threshold:
            flags.append({
                "flag_type": "job_hopping",
                "severity": "medium" if short_tenures < 4 else "high",
                "title": f"{short_tenures} roles with less than 1 year tenure",
                "description": _sanitize_text(
                    f"The candidate has {short_tenures} positions lasting less than 1 year. "
                    f"While short tenures can reflect contract work or startups, "
                    f"a pattern of very brief employment may indicate retention concerns."
                ),
                "evidence": f"{short_tenures} of {len(sorted_exps)} roles lasted less than 1 year",
                "suggestion": "Ask about the reasons for short tenures. Were these contracts, layoffs, or voluntary moves?",
            })

    # A7. Overlapping employment dates (all-pairs, month-level precision)
    if len(sorted_exps) >= 2:
        # Build parsed date ranges for all experiences
        date_ranges = []
        for exp in sorted_exps:
            start_m, end_m = _parse_experience_dates(exp)
            if start_m is not None and end_m is not None:
                date_ranges.append((start_m, end_m, exp))

        # Check all pairs for overlaps (not just consecutive)
        flagged_pairs = set()
        for i in range(len(date_ranges)):
            for j in range(i + 1, len(date_ranges)):
                start_a, end_a, exp_a = date_ranges[i]
                start_b, end_b, exp_b = date_ranges[j]

                # Two ranges overlap if one starts before the other ends
                overlap_months = min(end_a, end_b) - max(start_a, start_b)

                if overlap_months >= 2:  # At least 2 months overlap (filter out minor rounding)
                    company_a = exp_a.get("company") or "unknown company"
                    company_b = exp_b.get("company") or "unknown company"
                    pair_key = tuple(sorted([company_a.lower(), company_b.lower()]))

                    if pair_key in flagged_pairs:
                        continue  # Don't flag same pair twice
                    flagged_pairs.add(pair_key)

                    # Determine severity based on overlap length
                    if overlap_months >= 12:
                        severity = "medium"
                        overlap_desc = f"{overlap_months // 12} year{'s' if overlap_months >= 24 else ''}"
                    else:
                        severity = "low"
                        overlap_desc = f"{overlap_months} month{'s' if overlap_months != 1 else ''}"

                    start_a_str = exp_a.get("start_date") or "?"
                    end_a_str = exp_a.get("end_date") or "?"
                    start_b_str = exp_b.get("start_date") or "?"
                    end_b_str = exp_b.get("end_date") or "?"

                    flags.append({
                        "flag_type": "overlapping_dates",
                        "severity": severity,
                        "title": _sanitize_text(f"~{overlap_desc} overlap between {company_a} and {company_b}"),
                        "description": _sanitize_text(
                            f"The role at {company_a} ({start_a_str} - {end_a_str}) "
                            f"overlaps by approximately {overlap_desc} with the role at "
                            f"{company_b} ({start_b_str} - {end_b_str}). "
                            f"This could indicate concurrent positions, consulting, "
                            f"a transition period, or approximate dates."
                        ),
                        "evidence": _sanitize_text(
                            f"{company_a}: {start_a_str} - {end_a_str}, "
                            f"{company_b}: {start_b_str} - {end_b_str}"
                        ),
                        "suggestion": "Clarify whether these roles were concurrent (e.g., part-time, consulting) or if the dates are approximate.",
                    })

    # A8. Career trajectory — downward moves without explanation (using sorted experiences)
    # Uses the proper numeric seniority scale (1-8) from experience_trajectory
    # instead of crude 3-bucket keyword matching. A "downward move" requires a
    # drop of >= 1.5 levels (e.g., Manager 5 → Analyst 2.5 = 2.5 drop ✓,
    # but Manager 5 → Senior 4 = 1.0 drop ✗ — that's a normal lateral move).
    if len(sorted_exps) >= 2:
        _DOWNWARD_THRESHOLD = 1.5  # minimum level drop to flag

        prev_level = None
        for i, exp in enumerate(sorted_exps):
            title = exp.get("title", "") or ""
            curr_level = _get_seniority_level(title)

            if prev_level is not None and i > 0:
                drop = prev_level - curr_level
                if drop >= _DOWNWARD_THRESHOLD:
                    flags.append({
                        "flag_type": "career_trajectory",
                        "severity": "low",
                        "title": "Potential downward career move detected",
                        "description": _sanitize_text(
                            f"The candidate moved from '{sorted_exps[i-1].get('title', 'senior role')}' "
                            f"at {sorted_exps[i-1].get('company', 'previous company')} to "
                            f"'{exp.get('title', 'less senior role')}' at {exp.get('company', 'current company')}. "
                            f"This could indicate a career pivot, company change, or other circumstances worth exploring."
                        ),
                        "evidence": f"From: {sorted_exps[i-1].get('title', '?')} (level {prev_level}), To: {exp.get('title', '?')} (level {curr_level})",
                        "suggestion": "Ask about the motivation for this transition. Was it a pivot, a startup move, or a different kind of growth?",
                    })
                    break  # Only flag the most significant drop
            prev_level = curr_level

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
        education_data = parsed_resume.get("education") or []
        has_any_degree = any(
            edu.get("degree") or edu.get("institution")
            for edu in education_data if isinstance(edu, dict)
        ) if education_data else False

        # Check for professional certifications before flagging education gap
        certifications = parsed_resume.get("certifications") or []
        has_certifications = bool(certifications)

        if not has_any_degree and not has_certifications:
            flags.append({
                "flag_type": "education_gap",
                "severity": "low",
                "title": "No formal education or certifications detected on resume",
                "description": _sanitize_text(
                    "No degree, formal education, or professional certifications were found on the resume. "
                    "While not always required, many roles expect at least some "
                    "formal training or certification. "
                    "This may also indicate the resume is incomplete."
                ),
                "evidence": "No education entries or certifications found in parsed resume",
                "suggestion": "Ask about the candidate's educational background and any certifications not listed on the resume.",
            })

    # ═══════════════════════════════════════════════════════════════════
    # C. Universal scoring flags (soft skills, trajectory, industry)
    # ═══════════════════════════════════════════════════════════════════

    role_type_name = role_type.get("type", "skill_heavy")

    # C1. Soft skill gaps (especially important for hybrid/experience-heavy roles)
    if role_type_name in ("hybrid", "experience_heavy"):
        try:
            soft_skill_gaps = get_soft_skill_gaps_for_role(
                soft_skills, job_title=job_title, role_type=role_type_name
            )
        except Exception as e:
            logger.warning(f"Soft skill gap detection failed: {e}")
            soft_skill_gaps = []
        for gap in soft_skill_gaps:
            flags.append({
                "flag_type": "soft_skill_gap",
                "severity": gap.get("severity", "low"),
                "title": _sanitize_text(gap.get("title", "")),
                "description": _sanitize_text(gap.get("description", "")),
                "evidence": f"Soft skill proxy analysis found no evidence of {gap.get('category', 'skill')}",
                "suggestion": f"Probe {gap.get('category', 'this area')} experience in the interview with behavioral questions.",
            })

    # C2. Career trajectory concerns
    progression_type = trajectory.get("progression_type", "")
    trajectory_score = trajectory.get("trajectory_score", 0)

    if progression_type == "descending" and trajectory_score < 40:
        flags.append({
            "flag_type": "trajectory_concern",
            "severity": "medium",
            "title": "Downward career trajectory detected",
            "description": _sanitize_text(
                f"The candidate shows a descending seniority pattern across their career history. "
                f"Trajectory score: {trajectory_score}/100. "
                f"This could indicate career challenges or a deliberate pivot."
            ),
            "evidence": trajectory.get("trajectory_summary", ""),
            "suggestion": "Ask about career transitions and motivations for role changes.",
        })

    if trajectory.get("gap_count", 0) >= 2 and trajectory.get("gap_months", 0) > 18:
        flags.append({
            "flag_type": "career_gaps",
            "severity": "low",
            "title": f"Multiple career gaps detected ({trajectory.get('gap_count', 0)} gaps, ~{trajectory.get('gap_months', 0)} months total)",
            "description": _sanitize_text(
                f"The candidate has {trajectory.get('gap_count', 0)} career gaps "
                f"totaling approximately {trajectory.get('gap_months', 0)} months. "
                f"This may reflect personal circumstances, market conditions, or other factors."
            ),
            "evidence": trajectory.get("trajectory_summary", ""),
            "suggestion": "Ask about the gaps in a supportive way to understand context.",
        })

    # C3. Industry mismatch (for experience-heavy roles where industry matters)
    if role_type_name == "experience_heavy":
        industry_match = trajectory.get("industry_match", 0.5)
        if industry_match < 0.2 and trajectory.get("total_years", 0) > 3:
            flags.append({
                "flag_type": "industry_mismatch",
                "severity": "medium",
                "title": "Low industry alignment with target role",
                "description": _sanitize_text(
                    f"The candidate's career history shows minimal overlap with the target role's industry. "
                    f"Industry match score: {round(industry_match * 100)}%. "
                    f"For experience-heavy roles, industry knowledge is often important."
                ),
                "evidence": trajectory.get("trajectory_summary", ""),
                "suggestion": "Assess transferable domain knowledge and willingness to learn the new industry.",
            })

    # ═══════════════════════════════════════════════════════════════════
    # D. Domain-fit flags (industry-specific risk assessment)
    # ═══════════════════════════════════════════════════════════════════

    domain_match = domain_fit.get("domain_match", "domain_agnostic")
    jd_domain = domain_fit.get("jd_domain")
    domain_risk = domain_fit.get("domain_risk_summary", "")
    domain_gaps = domain_fit.get("domain_gaps", [])

    if jd_domain and domain_match in ("adjacent", "out_of_domain"):
        domain_label = jd_domain.replace("_", " ").title()
        severity = "high" if domain_match == "out_of_domain" else "medium"

        flags.append({
            "flag_type": "domain_fit",
            "severity": severity,
            "title": _sanitize_text(
                f"{'No' if domain_match == 'out_of_domain' else 'Limited'} "
                f"{domain_label} domain experience"
            ),
            "description": _sanitize_text(domain_risk),
            "evidence": _sanitize_text(
                f"JD domain: {domain_label}. "
                f"Candidate domains: {', '.join(d['domain'].replace('_', ' ').title() for d in domain_fit.get('candidate_domains', [])[:3]) or 'Not identified'}."
            ),
            "suggestion": _sanitize_text(
                f"Validate {domain_label}-specific experience in the interview. "
                + (f"Key domain gaps: {', '.join(domain_gaps[:4])}." if domain_gaps else "")
            ),
        })

    # D2. Domain-specific skill gaps (when JD has domain-critical skills the candidate lacks)
    if domain_gaps and jd_domain:
        for gap_skill in domain_gaps[:3]:  # Cap at 3 domain-specific gaps
            flags.append({
                "flag_type": "domain_skill_gap",
                "severity": "medium",
                "title": _sanitize_text(f"No evidence of {gap_skill}"),
                "description": _sanitize_text(
                    f"The JD specifically requires {gap_skill}, which is a domain-critical "
                    f"skill for {jd_domain.replace('_', ' ')} roles. No evidence was found "
                    f"on the candidate's resume."
                ),
                "evidence": f"Domain-critical skill for {jd_domain.replace('_', ' ')} roles",
                "suggestion": f"Ask specifically about {gap_skill} experience and willingness to develop.",
            })

    return flags


# ── Shared date parsing utilities ────────────────────────────────────

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _extract_month(date_str: str) -> int:
    """Extract month number from a date string. Returns 6 (mid-year) if unknown."""
    if not date_str:
        return 6
    # Try "YYYY-MM" or "YYYY/MM" (ISO format — common from resume parsers)
    iso_m = re.search(r'(?:20|19)\d{2}\s*[/\-]\s*(\d{1,2})(?:\b|$)', date_str)
    if iso_m:
        month = int(iso_m.group(1))
        if 1 <= month <= 12:
            return month
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


def _extract_year(date_str: str) -> int | None:
    """Extract a 4-digit year from a date string. Returns None if not found."""
    if not date_str:
        return None
    match = re.search(r'(20\d{2}|19\d{2})', str(date_str))
    return int(match.group(1)) if match else None


def _is_present_date(date_str: str) -> bool:
    """Check if a date string indicates 'present' / 'current'."""
    if not date_str:
        return False
    lower = str(date_str).lower()
    return "present" in lower or "current" in lower


def _date_to_months(date_str: str, fallback_year: int | None = None) -> int | None:
    """
    Convert a date string to an absolute month count (year*12 + month).
    This allows easy arithmetic between dates.
    If 'present'/'current', uses current year/month.
    Returns None if the date can't be parsed.
    """
    if not date_str:
        if fallback_year:
            return fallback_year * 12 + 6
        return None
    if _is_present_date(date_str):
        now = datetime.now()
        return now.year * 12 + now.month
    year = _extract_year(date_str)
    if year is None:
        if fallback_year:
            return fallback_year * 12 + 6
        return None
    month = _extract_month(date_str)
    return year * 12 + month


def _parse_experience_dates(exp: dict) -> tuple[int | None, int | None]:
    """
    Parse start and end dates from an experience entry into absolute months.
    Returns (start_months, end_months). Either can be None if unparseable.
    """
    start_str = exp.get("start_date") or ""
    end_str = exp.get("end_date") or ""
    start = _date_to_months(start_str)
    end = _date_to_months(end_str)
    return start, end


def _sort_experiences_by_start(experiences: list[dict]) -> list[dict]:
    """
    Sort experience entries chronologically by start date (earliest first).
    Entries without parseable start dates go to the end.
    """
    def sort_key(exp):
        start_str = exp.get("start_date") or ""
        start = _date_to_months(start_str)
        return start if start is not None else 999999
    return sorted(experiences, key=sort_key)


def _estimate_gap_months(end_date_str: str, start_date_str: str) -> int | None:
    """Estimate gap in months between two date strings."""
    if not end_date_str or not start_date_str:
        return None
    if _is_present_date(end_date_str):
        return None

    end = _date_to_months(end_date_str)
    start = _date_to_months(start_date_str)
    if end is None or start is None:
        return None

    gap = start - end
    return max(0, gap)


# ═══════════════════════════════════════════════════════════════════════
# Bias detection and adverse impact analysis
# ═══════════════════════════════════════════════════════════════════════

def compute_adverse_impact_metrics(analysis_results: list[dict]) -> dict:
    """
    Compute adverse impact metrics for a batch of analysis results.
    Implements the four-fifths (80%) rule from EEOC Uniform Guidelines.

    This function checks whether the scoring system produces systematically
    different outcomes across detectable demographic dimensions.

    Args:
        analysis_results: List of dicts with keys:
            - overall_score, recommendation, candidate_name, education_level,
              years_experience, location (optional)

    Returns:
        dict with fairness metrics, warnings, and recommendations
    """
    if len(analysis_results) < 4:
        return {
            "status": "insufficient_data",
            "message": "Need at least 4 candidates for meaningful bias analysis.",
            "metrics": {},
        }

    scores = [r.get("overall_score", 0) for r in analysis_results]
    recs = [r.get("recommendation", "maybe") for r in analysis_results]

    # ── Score distribution analysis ─────────────────────────────────
    mean_score = sum(scores) / len(scores)
    variance = sum((s - mean_score) ** 2 for s in scores) / len(scores)
    std_dev = variance ** 0.5

    # ── Recommendation distribution ─────────────────────────────────
    rec_counts = {}
    for r in recs:
        rec_counts[r] = rec_counts.get(r, 0) + 1

    positive_rate = sum(1 for r in recs if r in ("yes", "strong_yes")) / len(recs)
    negative_rate = sum(1 for r in recs if r in ("no", "strong_no")) / len(recs)

    # ── Experience-based fairness check ─────────────────────────────
    # Check if candidates with similar experience get wildly different scores
    # (proxy for age-related bias)
    exp_groups = {"junior": [], "mid": [], "senior": []}
    for r in analysis_results:
        years = r.get("years_experience") or 0
        if years < 5:
            exp_groups["junior"].append(r.get("overall_score", 0))
        elif years < 12:
            exp_groups["mid"].append(r.get("overall_score", 0))
        else:
            exp_groups["senior"].append(r.get("overall_score", 0))

    exp_warnings = []
    exp_means = {}
    for group, group_scores in exp_groups.items():
        if group_scores:
            exp_means[group] = sum(group_scores) / len(group_scores)

    # Four-fifths rule check between experience groups
    # NOTE: This applies the four-fifths rule to score distributions as a proxy for fairness,
    # not to selection rates (which is the EEOC standard). Use this as a warning signal
    # for potential bias in the scoring system across demographic groups.
    if len(exp_means) >= 2:
        max_mean = max(exp_means.values())
        for group, group_mean in exp_means.items():
            if max_mean > 0 and group_mean / max_mean < 0.80:
                exp_warnings.append(
                    f"{group.capitalize()} experience group ({group_mean:.0%} avg) scores below "
                    f"80% of the highest group ({max_mean:.0%} avg). "
                    f"This may indicate experience-level bias in scoring."
                )

    # ── Education-based fairness check ──────────────────────────────
    edu_groups = {}
    for r in analysis_results:
        edu = (r.get("education_level") or "unknown").lower()
        if edu not in edu_groups:
            edu_groups[edu] = []
        edu_groups[edu].append(r.get("overall_score", 0))

    edu_warnings = []
    if len(edu_groups) >= 2:
        edu_means = {g: sum(s) / len(s) for g, s in edu_groups.items() if s}
        max_edu_mean = max(edu_means.values()) if edu_means else 0
        for group, group_mean in edu_means.items():
            if max_edu_mean > 0 and group_mean / max_edu_mean < 0.80 and len(edu_groups.get(group, [])) >= 2:
                edu_warnings.append(
                    f"Candidates with '{group}' education ({group_mean:.0%} avg) score below "
                    f"80% of the highest education group ({max_edu_mean:.0%} avg). "
                    f"Consider whether education weight is appropriate for this role."
                )

    # ── Score clustering check ──────────────────────────────────────
    # Detect if scores are artificially clustered (all same) or have suspicious patterns
    clustering_warning = None
    if std_dev < 0.03 and len(scores) >= 5:
        clustering_warning = (
            "Score variance is extremely low — all candidates received nearly identical scores. "
            "This may indicate the scoring criteria are too generic for this role."
        )
    elif std_dev > 0.35:
        clustering_warning = (
            "Score variance is very high — candidates are being scored with extreme spread. "
            "Consider whether the required skill list is too narrow or too specialized."
        )

    # ── Compile results ─────────────────────────────────────────────
    all_warnings = exp_warnings + edu_warnings
    if clustering_warning:
        all_warnings.append(clustering_warning)

    return {
        "status": "pass" if not all_warnings else "review_recommended",
        "candidate_count": len(analysis_results),
        "metrics": {
            "mean_score": round(mean_score, 3),
            "std_deviation": round(std_dev, 3),
            "positive_rate": round(positive_rate, 3),
            "negative_rate": round(negative_rate, 3),
            "recommendation_distribution": rec_counts,
        },
        "experience_group_means": {k: round(v, 3) for k, v in exp_means.items()},
        "warnings": all_warnings,
        "guidance": (
            "All checks passed. No adverse impact patterns detected."
            if not all_warnings else
            "Review the warnings above. Consider adjusting scoring weights or "
            "skill requirements if patterns suggest systematic disadvantage to any group. "
            "Per EEOC Uniform Guidelines, selection rates below 80% of the highest group "
            "may indicate adverse impact requiring further investigation."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════
# Interview question generation
# ═══════════════════════════════════════════════════════════════════════

def _generate_interview_questions(
    assessments, scores: dict, required_skills: list, risk_flags: list,
    resume_parsed: dict = None, candidate_name: str = "the candidate",
    job_title: str = "this role", role_type: str = "skill_heavy",
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

    # ── Helper: truncate text at word boundary ──────────────────────────
    def _truncate(text: str, max_len: int = 120) -> str:
        """Truncate text at a word boundary, avoiding mid-word cuts."""
        if len(text) <= max_len:
            return text
        truncated = text[:max_len].rsplit(" ", 1)[0]
        return truncated if truncated else text[:max_len]

    # ── Helper: extract concrete evidence from an assessment ──────────
    def _get_evidence_details(assessment) -> dict:
        """Pull specific projects, tools, and context from evidence."""
        projects = []
        source_snippets = []
        for ev in (assessment.evidence or []):
            # Skip generic skills-list evidence — not useful for interview questions
            ev_type = getattr(ev, "evidence_type", "") or ""
            if ev_type == "skills_list":
                continue
            desc = (ev.description or "").strip()
            src = (ev.source_text or "").strip()
            if desc and len(desc) > 10 and desc.lower() not in ("listed in skills section",):
                projects.append(desc)
            if src and len(src) > 10:
                source_snippets.append(src[:150])
        return {"projects": projects[:3], "snippets": source_snippets[:3]}

    # ── Helper: get candidate's experience entries for context ────────
    experience_entries = resume_parsed.get("experience") or []
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
                    f"Your resume mentions {a.name} in the context of: \"{_truncate(project_ref)}\" — "
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
                    f"Tell me about a time you had to solve a particularly complex problem or lead an initiative "
                    f"involving {a.name}. What made it challenging?"
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
            # Strategy 1: Same category match (language, framework, etc.)
            # Strategy 2: Adjacency graph neighbors
            related = []
            missing_category = req.get("category", "")
            missing_canonical = _normalize_skill(skill_name)

            # Strategy 1: Category-based matching
            for a in assessments:
                if a.estimated_depth >= 2 and _normalize_skill(a.name) != missing_canonical:
                    if a.category and missing_category and a.category.lower() == missing_category.lower():
                        related.append(a.name)

            # Strategy 2: If no category matches, check adjacency graph neighbors
            if not related:
                # Find skills that share adjacency targets with the missing skill
                missing_adjacencies = set(
                    t[0] for t in _SKILL_ADJACENCY.get(missing_canonical, [])
                )
                for a in assessments:
                    if a.estimated_depth >= 2 and _normalize_skill(a.name) != missing_canonical:
                        a_canonical = _normalize_skill(a.name)
                        a_adjacencies = set(
                            t[0] for t in _SKILL_ADJACENCY.get(a_canonical, [])
                        )
                        # If they share foundation skills, they're related
                        if missing_adjacencies & a_adjacencies:
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
                    f"or with closely related skills that would help you ramp up?"
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

            # Use LLM reasoning when available (skill-specific), fall back to evidence
            reasoning_short = _truncate(reasoning, 100) if reasoning else ""

            # Varied question templates for experience_heavy roles to avoid monotony
            _exp_heavy_gap_templates = [
                (
                    f"Your background shows some exposure to {skill_name}, but this "
                    f"{job_title} role requires deeper expertise. "
                    f"Can you walk us through how you've applied {skill_name} at a strategic level "
                    f"and what outcomes you drove?"
                ),
                (
                    f"For {skill_name}, the role expects advanced-level proficiency. "
                    f"Tell us about a time when {skill_name} was critical to a business outcome you were responsible for. "
                    f"What was the scope and what did you achieve?"
                ),
                (
                    f"This role requires strong {skill_name} capabilities. "
                    f"How has {skill_name} featured in your most senior responsibilities, "
                    f"and where would you say your depth is strongest?"
                ),
                (
                    f"We'd like to understand your {skill_name} depth better. "
                    f"Can you describe a situation where you had to make a significant decision "
                    f"involving {skill_name}, and what the impact was?"
                ),
                (
                    f"The {job_title} role needs someone who can own {skill_name} end-to-end. "
                    f"What's the most complex {skill_name} challenge you've tackled, "
                    f"and how did you approach it?"
                ),
            ]

            if role_type == "experience_heavy":
                # Use gap_question_counter (based on priority_counter) to rotate templates
                template_idx = (priority_counter - 1) % len(_exp_heavy_gap_templates)
                question = _exp_heavy_gap_templates[template_idx]
            elif ev["projects"]:
                project_ref = _truncate(ev["projects"][0])
                question = (
                    f"For {skill_name}, we found evidence like \"{project_ref}\" — "
                    f"but the role needs deeper expertise (depth {min_depth} vs your current {actual_depth}). "
                    f"Can you walk us through a situation where you owned the end-to-end approach for {skill_name}?"
                )
            else:
                actual_label = _depth_label(actual_depth)
                needed_label = _depth_label(min_depth)
                question = (
                    f"Your {skill_name} experience appears to be at a {actual_label} level, "
                    f"but this role needs {needed_label}. "
                    f"What's the most complex or high-stakes situation where you applied {skill_name}? "
                    f"Are there areas of it you haven't had the chance to work on yet?"
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
                if role_type == "experience_heavy":
                    question = (
                        f"Your resume mentions {skill} in the context of \"{ev['projects'][0][:80]}\" — "
                        f"but the evidence is lighter than expected for the depth claimed. "
                        f"Can you walk me through your specific contribution, the scope of your ownership, "
                        f"and the outcomes you delivered?"
                    )
                else:
                    question = (
                        f"Your resume lists {skill} and mentions \"{ev['projects'][0][:80]}\" — "
                        f"but the evidence seems lighter than expected for the depth level claimed. "
                        f"Can you take me through exactly what you built, what decisions were yours, "
                        f"and what you'd do differently today?"
                    )
            else:
                if role_type == "experience_heavy":
                    question = (
                        f"You've listed {skill} on your resume, but we couldn't find detailed evidence of it in practice. "
                        f"Can you describe a specific situation where {skill} was central to your work, "
                        f"including the context, your role, and the outcome?"
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
            if role_type == "experience_heavy":
                question = (
                    f"There appears to be a gap in your work history. "
                    f"During that period, were you doing anything relevant to your career — "
                    f"consulting, advisory roles, further education, or professional development? "
                    f"Any of those can count as valid experience."
                )
            else:
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

        # stale_skill flags are no longer generated (removed in A5)
        # but guard in case old data is encountered
        elif flag_type == "stale_skill":
            pass  # Skip — recency is handled by score weighting, not surfaced as a flag

        elif flag_type == "seniority_mismatch":
            if role_type == "experience_heavy":
                question = (
                    f"This is a {job_title} position. "
                    f"Can you give me an example of a time you led a major initiative end-to-end — "
                    f"from scoping and stakeholder alignment through to delivery and measuring impact? "
                    f"What was the scale of your team, and how did you navigate competing priorities?"
                )
            else:
                question = (
                    f"This is a {job_title} position. "
                    f"Can you give me an example of a time you led an initiative end-to-end — "
                    f"from requirements gathering through to delivery and monitoring? "
                    f"What was your team size and how did you handle disagreements?"
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
            if role_type == "experience_heavy":
                question = (
                    f"Looking at your background, there are some gaps relative to this {job_title} role. "
                    f"Beyond what's on your resume, do you have other relevant experience — "
                    f"consulting engagements, board roles, industry certifications, or projects "
                    f"that might demonstrate your capabilities in this area?"
                )
            else:
                question = (
                    f"Looking at your background, there are some gaps relative to this {job_title} role. "
                    f"Beyond what's on your resume, what other experience or projects "
                    f"do you have that might be relevant? Sometimes candidates don't list everything — "
                    f"side projects, certifications, or contributions can all be valuable."
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
                f"Which of these was the most challenging, and what would you improve "
                f"if you were to redo it today?"
            )
        elif ev["projects"]:
            question = (
                f"Your {a.name} work on \"{ev['projects'][0][:80]}\" stood out. "
                f"What was the scale of this initiative and what was the biggest challenge "
                f"you had to navigate? How did your approach evolve over time?"
            )
        elif recent_companies:
            question = (
                f"Your {a.name} expertise looks solid based on your work at {recent_companies[0]}. "
                f"Tell me about the most impactful initiative you led with {a.name} there — "
                f"what business problem did it solve and how did you measure success?"
            )
        else:
            question = (
                f"Your {a.name} skills rate very well for this role. "
                f"Can you walk me through a situation where you applied {a.name} to drive a significant outcome? "
                f"I want to understand the depth of your experience."
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
            + f". What kind of team culture brings out your best work? "
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
                     experience_range: dict = None, job_title: str = "",
                     role_type: dict = None, trajectory: dict = None,
                     soft_skills: dict = None) -> dict:
    """
    Compute capability scores from job-focused pipeline assessments.
    Includes recency weighting, impact markers, adjacency-boosted skills,
    experience range validation, and adaptive scoring based on role type.

    New in universal scoring:
    - role_type: Adjusts weight multipliers (skill-heavy vs experience-heavy)
    - trajectory: Career progression score (0-100) from experience_trajectory module
    - soft_skills: Soft skill proxy detection results
    """
    if parsed_resume is None:
        parsed_resume = {}
    if experience_range is None:
        experience_range = {}
    if role_type is None:
        role_type = {"type": "skill_heavy", "confidence": 0.5, "scoring_weights": {}}
    if trajectory is None:
        trajectory = {}
    if soft_skills is None:
        soft_skills = {}

    # ── Adaptive scoring weights based on role type ───────────────────
    weight_mults = role_type.get("scoring_weights", {})
    w_skill_raw = weight_mults.get("skill_match", 1.0)
    w_depth_raw = weight_mults.get("depth", 1.0)
    w_experience_raw = weight_mults.get("experience", 1.0)
    w_education_raw = weight_mults.get("education", 1.0)
    w_trajectory = weight_mults.get("trajectory", 1.0)
    w_soft_skill = weight_mults.get("soft_skill_proxy", 1.0)

    # Normalize base-component multipliers so the weighted sum always equals 0.85
    # (the design target). Without this, experience-heavy roles have a structural
    # scoring ceiling ~71% vs ~91% for skill-heavy roles — unfairly penalizing
    # non-tech candidates regardless of actual fit.
    _BASE_COEFFICIENTS = [0.35, 0.22, 0.18, 0.10]  # skill, depth, exp, edu
    _raw_mults = [w_skill_raw, w_depth_raw, w_experience_raw, w_education_raw]
    _weighted_sum = sum(b * m for b, m in zip(_BASE_COEFFICIENTS, _raw_mults))
    _norm_factor = 0.85 / _weighted_sum if _weighted_sum > 0 else 1.0
    w_skill = w_skill_raw * _norm_factor
    w_depth = w_depth_raw * _norm_factor
    w_experience = w_experience_raw * _norm_factor
    w_education = w_education_raw * _norm_factor

    # Build lookup: normalized skill name → assessment
    skill_map = {}
    for a in assessments:
        skill_map[_normalize_skill(a.name)] = a

    # Extract impact markers for bonus scoring
    impact_markers = _extract_impact_markers(parsed_resume)

    breakdown = {}
    weighted_match_sum = 0.0
    total_weight = 0.0
    depth_scores = []
    strengths = []
    gaps = []

    for req in required_skills:
        skill_name = req.get("skill", "")
        min_depth = req.get("min_depth", 2)
        weight = req.get("weight", 1.0)
        category = req.get("category", "unknown")

        assessment = skill_map.get(_normalize_skill(skill_name))
        total_weight += weight

        if assessment and assessment.estimated_depth > 0:
            # Apply recency weighting
            recency = _compute_recency_factor(assessment, parsed_resume)
            effective_depth = assessment.estimated_depth * recency

            meets_depth = assessment.estimated_depth >= min_depth
            # Human-readable depth labels
            depth_label = _depth_label(assessment.estimated_depth)
            required_label = _depth_label(min_depth)
            confidence_pct = f"{assessment.depth_confidence:.0%}"
            reasoning_short = assessment.depth_reasoning[:200] if assessment.depth_reasoning else ""

            if meets_depth:
                weighted_match_sum += weight  # Full credit (weighted)
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
                # Partial credit: candidate has the skill but below required depth
                # depth 2 of required 3 gets 0.67 * weight credit (not zero)
                partial_ratio = assessment.estimated_depth / max(min_depth, 1)
                partial_credit = partial_ratio * weight * 0.75  # 75% of proportional credit
                weighted_match_sum += partial_credit
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
                "category": category,
                "recency_factor": round(recency, 2),
                "reasoning": _sanitize_text(reasoning_short),
            }
        else:
            # Skill completely missing from resume — zero credit
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
                "category": category,
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
            reasoning_short = assessment.depth_reasoning[:200] if assessment.depth_reasoning else ""
            strengths.append(
                f"Has preferred skill: {skill_name}, "
                f"rated {depth_label} (depth {assessment.estimated_depth}). "
                f"{confidence_pct} confidence. {reasoning_short}"
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

    # Weighted skill match: accounts for skill importance and partial credit
    skill_match = weighted_match_sum / total_weight if total_weight > 0 else 0.5
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
    preferred_bonus = (preferred_matched / total_preferred) * 0.05 if preferred_skills else 0.0

    # Impact bonus: candidates with quantified achievements get a small boost (0-0.03)
    impact_bonus = min(len(impact_markers) * 0.005, 0.03)

    # ── Trajectory score ──────────────────────────────────────────────
    trajectory_score_raw = trajectory.get("trajectory_score", 0) / 100.0  # Normalize to 0-1
    trajectory_bonus = trajectory_score_raw * 0.05 * w_trajectory  # Up to 5% with weight

    # ── Soft skill proxy score ────────────────────────────────────────
    soft_skill_score_raw = soft_skills.get("soft_skill_score", 0) / 100.0  # Normalize to 0-1
    soft_skill_bonus = soft_skill_score_raw * 0.04 * w_soft_skill  # Up to 4% with weight

    # Overall score: weighted composite with adaptive role-type multipliers
    # Base weights: skill_match 35%, depth 22%, experience 18%, education 10% = 85%
    #   trajectory up to 5%, soft skills up to 4%, preferred up to 5%,
    #   impact up to 3%, perfect match 2%, leadership up to 4%
    # Penalty: experience shortfall up to -15%
    overall = (
        (skill_match * 0.35 * w_skill) +
        (depth_avg * 0.22 * w_depth) +
        (experience_score * 0.18 * w_experience) +
        (education_score * 0.10 * w_education) +
        (trajectory_bonus) +
        (soft_skill_bonus) +
        (preferred_bonus) +
        (impact_bonus) +
        (leadership_bonus) +
        (0.02 if skill_match >= 0.95 else 0.0) -
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

    # ── Confidence score: how reliable is this analysis? ─────────────
    # High confidence = lots of evidence, high LLM confidence, many skills matched
    # Low confidence = thin resume, few evidence items, uncertain skill assessments
    avg_confidence = sum(a.depth_confidence for a in assessments) / max(len(assessments), 1)
    evidence_count = sum(len(a.evidence) for a in assessments)
    skills_with_evidence = sum(1 for a in assessments if len(a.evidence) >= 1)

    confidence_factors = [
        min(avg_confidence, 1.0),                                      # LLM assessment confidence
        min(evidence_count / max(len(assessments) * 2, 1), 1.0),       # Evidence density
        skills_with_evidence / max(len(assessments), 1),               # Coverage completeness
        min(len(parsed_resume.get("experience") or []) / 3, 1.0),     # Resume richness
    ]
    analysis_confidence = round(sum(confidence_factors) / len(confidence_factors), 3)

    # ── Confidence interval: score range based on uncertainty ──────────
    # Width is inversely proportional to analysis confidence
    # At 0.9 confidence → ±2% range. At 0.3 confidence → ±12% range.
    interval_half_width = round((1 - analysis_confidence) * 0.15, 3)
    score_low = round(max(0, overall - interval_half_width), 3)
    score_high = round(min(1.0, overall + interval_half_width), 3)

    # ── Per-skill uncertainty flags ───────────────────────────────────
    # Flag skills with high impact on score but low confidence
    uncertain_skills = []
    for a in assessments:
        if a.depth_confidence < 0.5 and a.estimated_depth >= 2:
            # This skill claims to exist but the LLM isn't confident
            uncertain_skills.append({
                "skill": a.name,
                "depth": a.estimated_depth,
                "confidence": a.depth_confidence,
                "flag": "Low confidence assessment — verify in interview",
            })
        elif a.depth_confidence < 0.3 and a.estimated_depth == 0:
            # LLM isn't even confident it's missing
            uncertain_skills.append({
                "skill": a.name,
                "depth": 0,
                "confidence": a.depth_confidence,
                "flag": "Uncertain whether skill is present — resume may lack detail",
            })

    # Confidence-based recommendation adjustment
    # If confidence is very low, downgrade strong recommendations
    if analysis_confidence < 0.35 and recommendation in ("strong_yes", "strong_no"):
        recommendation = "yes" if recommendation == "strong_yes" else "no"
        confidence_note = "Recommendation tempered due to low analysis confidence."
    elif analysis_confidence < 0.25:
        recommendation = "maybe"
        confidence_note = "Low-confidence analysis — recommend manual review."
    else:
        confidence_note = None

    # ── Score explainability: what drove the score ────────────────────
    score_drivers = []
    if skill_match >= 0.80:
        score_drivers.append(f"Strong skill coverage ({round(skill_match*100)}% match)")
    elif skill_match < 0.50:
        score_drivers.append(f"Low skill coverage ({round(skill_match*100)}% match)")
    if depth_avg >= 0.70:
        score_drivers.append(f"Deep expertise across matched skills")
    elif depth_avg < 0.40:
        score_drivers.append(f"Surface-level depth on most skills")
    if experience_penalty > 0.05:
        score_drivers.append(f"Experience shortfall penalty (-{round(experience_penalty*100)}%)")
    if preferred_bonus > 0.03:
        score_drivers.append(f"Preferred skills bonus (+{round(preferred_bonus*100)}%)")
    if impact_bonus > 0.01:
        score_drivers.append(f"Quantified impact markers bonus (+{round(impact_bonus*100)}%)")
    if leadership_bonus > 0.01:
        score_drivers.append(f"Leadership signals bonus (+{round(leadership_bonus*100)}%)")
    if trajectory_bonus > 0.02:
        prog_type = trajectory.get("progression_type", "")
        score_drivers.append(f"Career trajectory bonus (+{round(trajectory_bonus*100)}%, {prog_type} progression)")
    if soft_skill_bonus > 0.02:
        strongest = soft_skills.get("strongest_areas", [])
        score_drivers.append(f"Soft skill evidence bonus (+{round(soft_skill_bonus*100)}%, strong in {', '.join(strongest[:2])})")
    # Role type context
    role_type_name = role_type.get("type", "skill_heavy")
    if role_type_name != "skill_heavy":
        score_drivers.append(f"Adaptive scoring applied: {role_type_name} role (weights adjusted)")

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
        # New: Explainability and confidence fields
        "analysis_confidence": analysis_confidence,
        "confidence_interval": {"low": score_low, "high": score_high},
        "uncertain_skills": uncertain_skills,
        "confidence_note": confidence_note,
        "score_drivers": score_drivers,
        "score_weights": {
            "skill_match": round(0.35 * w_skill, 3),
            "depth": round(0.22 * w_depth, 3),
            "experience": round(0.18 * w_experience, 3),
            "education": round(0.10 * w_education, 3),
            "trajectory_bonus": round(trajectory_bonus, 3),
            "soft_skill_bonus": round(soft_skill_bonus, 3),
            "preferred_bonus": round(preferred_bonus, 3),
            "impact_bonus": round(impact_bonus, 3),
            "leadership_bonus": round(leadership_bonus, 3),
            "experience_penalty": round(-experience_penalty, 3),
        },
        # ── New universal scoring fields ───────────────────────────────
        "role_type": role_type.get("type", "skill_heavy"),
        "role_type_confidence": role_type.get("confidence", 0.0),
        "role_type_signals": role_type.get("signals", {}),
        "trajectory": {
            "score": trajectory.get("trajectory_score", 0),
            "progression_type": trajectory.get("progression_type", "unknown"),
            "growth_rate": trajectory.get("growth_rate", 0.0),
            "total_years": trajectory.get("total_years", 0.0),
            "current_seniority": trajectory.get("current_seniority", 0.0),
            "industry_match": trajectory.get("industry_match", 0.0),
            "summary": trajectory.get("trajectory_summary", ""),
        },
        "soft_skill_proxies": {
            "score": soft_skills.get("soft_skill_score", 0),
            "strongest_areas": soft_skills.get("strongest_areas", []),
            "weakest_areas": soft_skills.get("weakest_areas", []),
            "evidence_count": len(soft_skills.get("soft_skills", [])),
        },
    }


def _estimate_candidate_years(parsed_resume: dict) -> int | None:
    """
    Estimate total years of professional experience from parsed resume data.
    Uses interval merging to correctly handle overlapping employment periods.

    Example: Two concurrent roles 2015-2020 count as 5 years, not 10.
    Returns None if we can't determine experience.
    """
    experiences = parsed_resume.get("experience") or []
    if not experiences:
        return None

    # Build list of (start_month, end_month) intervals
    intervals = []
    for exp in experiences:
        start_m, end_m = _parse_experience_dates(exp)
        if start_m is not None and end_m is not None and end_m >= start_m:
            intervals.append((start_m, end_m))

    if not intervals:
        # Fallback: use the old span method if dates can't be parsed to months
        current_year = datetime.now().year
        earliest_start = None
        for exp in experiences:
            start = exp.get("start_date") or ""
            start_match = re.search(r'(20\d{2}|19\d{2})', str(start))
            if start_match:
                year = int(start_match.group(1))
                if earliest_start is None or year < earliest_start:
                    earliest_start = year
        if earliest_start is None:
            return None
        return max(current_year - earliest_start, 0)

    # Merge overlapping intervals to avoid double-counting
    intervals.sort(key=lambda x: x[0])
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            # Overlapping or adjacent — extend the current interval
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    # Sum total months across all merged intervals
    total_months = sum(end - start for start, end in merged)
    return max(round(total_months / 12), 0)


def _detect_leadership_signals(parsed_resume: dict) -> list:
    """
    Detect leadership, architecture, and mentoring signals in the resume.
    Returns a list of strength messages for detected signals.
    """
    signals = []
    experiences = parsed_resume.get("experience") or []

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
    text = text.replace(" \u2014 ", ", ")    # spaced emdash first
    text = text.replace("\u2014", ", ")       # bare emdash
    text = text.replace(" \u2013 ", ", ")     # spaced endash first
    text = text.replace("\u2013", ", ")        # bare endash
    text = text.replace(" - ", ", ")
    # Clean up double-spaces and space-comma artifacts
    while "  " in text:
        text = text.replace("  ", " ")
    text = text.replace(" ,", ",")
    return text


def _generate_summary(candidate, job, assessments, scores, domain_fit: dict = None) -> str:
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
        summary += "No skill gaps were identified against the job requirements. "
    elif gap_count <= 3:
        gap_skills = []
        for g in scores["gaps"][:3]:
            skill_part = g.split(":")[0].replace("Missing required skill", "").replace("Below requirement in", "").strip()
            if skill_part:
                gap_skills.append(skill_part)
        if gap_skills:
            summary += f"{gap_count} skill gap{'s' if gap_count > 1 else ''} identified that may need further evaluation. "
    else:
        summary += f"{gap_count} skill gaps were identified that may need further evaluation. "

    # ── Domain-fit context in summary ────────────────────────────────
    if domain_fit is None:
        domain_fit = {}
    domain_match = domain_fit.get("domain_match", "domain_agnostic")
    jd_domain = domain_fit.get("jd_domain")
    if jd_domain and domain_match in ("adjacent", "out_of_domain"):
        domain_label = jd_domain.replace("_", " ")
        if domain_match == "adjacent":
            summary += (
                f"The candidate has transferable experience but lacks direct {domain_label} "
                f"domain background, which should be validated in the interview. "
            )
        else:
            summary += (
                f"The candidate has no direct {domain_label} domain experience. "
                f"Industry-specific knowledge gaps may be significant for this role. "
            )

    rec_map = {
        "strong_yes": "This candidate is a strong match and is recommended to advance to the next stage.",
        "yes": "This candidate is a good fit and is recommended to proceed in the pipeline.",
        "maybe": "This candidate shows potential but has some gaps. Consider a focused screen to verify key areas before advancing.",
        "no": "This candidate does not appear to be a strong fit for this role based on the skill requirements.",
        "strong_no": "There is a significant mismatch between this candidate's profile and the role requirements.",
    }
    summary += rec_map.get(scores["recommendation"], "Review pending.")

    # ── Score-narrative consistency check ──────────────────────────────
    # Ensure the recommendation aligns with the stated scores/gaps
    overall = scores.get("overall", 0)
    rec = scores["recommendation"]

    # If overall score is high but recommendation is weak, add context
    if overall >= 0.70 and rec == "maybe":
        summary += " Note: the overall score is strong; the recommendation reflects specific gaps that may affect role fit."
    # If overall score is low but narrative sounds positive, add context
    elif overall < 0.50 and rec in ("yes", "strong_yes"):
        summary += " Note: while some strong areas were found, the overall match is moderate."
    # If there are many strengths but low score, explain why
    elif len(high_depth) >= 4 and overall < 0.55:
        summary += " Despite multiple strong skill matches, the overall score reflects depth gaps or experience factors."

    return _sanitize_text(summary)
