"""
Batch Analysis Runner — concurrent pipeline execution with:
  - Bounded concurrency pool (asyncio.Semaphore)
  - Shared pre-computation (parse job/resume once, reuse across pairs)
  - Existing analysis dedup (skip if already analyzed)
  - Real-time progress tracking via in-memory store
  - Persistent DB storage for batch history (BatchAnalysis model)
"""

import uuid
import time
import asyncio
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.analysis import AnalysisResult, RiskFlag, InterviewQuestion, BatchAnalysis
from app.models.skill import Skill, SkillEvidence
from app.services.skill_pipeline import skill_pipeline, assessment_to_dict, timings_to_dict

logger = logging.getLogger(__name__)

# Max concurrent pipeline runs (LLM calls). Conservative to avoid rate limits.
MAX_CONCURRENCY = 8


# ═══════════════════════════════════════════════════════════════════════
# Batch state tracking (in-memory for live polling)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class BatchItemResult:
    candidate_id: str
    candidate_name: str
    job_id: str
    job_title: str
    analysis_id: str = ""
    overall_score: float = 0.0
    recommendation: str = ""
    processing_time_ms: int = 0
    cached: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "candidate_name": self.candidate_name,
            "job_id": self.job_id,
            "job_title": self.job_title,
            "analysis_id": self.analysis_id,
            "overall_score": self.overall_score,
            "recommendation": self.recommendation,
            "processing_time_ms": self.processing_time_ms,
            "cached": self.cached,
            "error": self.error,
        }


@dataclass
class BatchState:
    batch_id: str
    status: str = "processing"  # processing | completed | partial_failure | failed
    total: int = 0
    completed: int = 0
    failed: int = 0
    cached: int = 0
    results: List[BatchItemResult] = field(default_factory=list)
    started_at: float = 0.0
    elapsed_ms: int = 0
    # Metadata for DB persistence
    candidate_ids: List[str] = field(default_factory=list)
    job_ids: List[str] = field(default_factory=list)
    job_titles: List[str] = field(default_factory=list)
    company_id: str = ""


# Global store: batch_id → BatchState (for live polling)
# Capped at MAX_STORED_BATCHES to prevent unbounded memory growth.
_batch_store: Dict[str, BatchState] = {}
MAX_STORED_BATCHES = 50


def _evict_old_batches():
    """Remove oldest completed batches when the store exceeds the cap."""
    if len(_batch_store) <= MAX_STORED_BATCHES:
        return
    # Sort by start time, keep the newest MAX_STORED_BATCHES
    sorted_ids = sorted(
        _batch_store.keys(),
        key=lambda bid: _batch_store[bid].started_at,
    )
    # Only evict completed/failed batches (never evict one that's still processing)
    for bid in sorted_ids:
        if len(_batch_store) <= MAX_STORED_BATCHES:
            break
        if _batch_store[bid].status != "processing":
            del _batch_store[bid]


def get_batch_state(batch_id: str) -> Optional[BatchState]:
    return _batch_store.get(batch_id)


def list_batch_states() -> List[BatchState]:
    return list(_batch_store.values())


# ═══════════════════════════════════════════════════════════════════════
# DB persistence helpers
# ═══════════════════════════════════════════════════════════════════════

async def _create_batch_record(state: BatchState):
    """Create the initial BatchAnalysis row when batch starts."""
    async with AsyncSessionLocal() as db:
        batch = BatchAnalysis(
            batch_id=state.batch_id,
            company_id=state.company_id or None,
            candidate_ids=state.candidate_ids,
            job_ids=state.job_ids,
            status="processing",
            total=state.total,
            completed=0,
            failed=0,
            cached=0,
            candidate_count=len(state.candidate_ids),
            job_titles=state.job_titles,
        )
        db.add(batch)
        await db.commit()
        logger.info(f"Batch {state.batch_id} saved to DB")


async def _finalize_batch_record(state: BatchState):
    """Update the BatchAnalysis row when batch completes."""
    # Compute summary stats
    successful_results = [r for r in state.results if not r.error]
    avg_score = 0.0
    top_rec = None
    if successful_results:
        avg_score = sum(r.overall_score for r in successful_results) / len(successful_results)
        # Most common recommendation among successful
        rec_counts: Dict[str, int] = {}
        for r in successful_results:
            rec_counts[r.recommendation] = rec_counts.get(r.recommendation, 0) + 1
        top_rec = max(rec_counts, key=rec_counts.get) if rec_counts else None

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(BatchAnalysis)
            .where(BatchAnalysis.batch_id == state.batch_id)
            .values(
                status=state.status,
                completed=state.completed,
                failed=state.failed,
                cached=state.cached,
                elapsed_ms=state.elapsed_ms,
                results=[r.to_dict() for r in state.results],
                avg_score=round(avg_score, 4),
                top_recommendation=top_rec,
                completed_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
        logger.info(f"Batch {state.batch_id} finalized in DB")


async def load_saved_batches(company_id: str = None) -> List[dict]:
    """Load saved batches from DB, most recent first. Filtered by company if provided."""
    async with AsyncSessionLocal() as db:
        query = select(BatchAnalysis).order_by(BatchAnalysis.created_at.desc())
        if company_id:
            query = query.where(BatchAnalysis.company_id == company_id)
        result = await db.execute(query)
        batches = result.scalars().all()
        return [
            {
                "batch_id": b.batch_id,
                "status": b.status,
                "total": b.total,
                "completed": b.completed,
                "failed": b.failed,
                "cached": b.cached,
                "elapsed_ms": b.elapsed_ms,
                "candidate_ids": b.candidate_ids,
                "job_ids": b.job_ids,
                "job_titles": b.job_titles,
                "candidate_count": b.candidate_count,
                "avg_score": b.avg_score,
                "top_recommendation": b.top_recommendation,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "completed_at": b.completed_at.isoformat() if b.completed_at else None,
                "results": b.results or [],
            }
            for b in batches
        ]


async def load_saved_batch(batch_id: str, company_id: str = None) -> Optional[dict]:
    """Load a single saved batch from DB. Verifies company if provided."""
    async with AsyncSessionLocal() as db:
        query = select(BatchAnalysis).where(BatchAnalysis.batch_id == batch_id)
        if company_id:
            query = query.where(BatchAnalysis.company_id == company_id)
        result = await db.execute(query)
        b = result.scalars().first()
        if not b:
            return None
        return {
            "batch_id": b.batch_id,
            "status": b.status,
            "total": b.total,
            "completed": b.completed,
            "failed": b.failed,
            "cached": b.cached,
            "elapsed_ms": b.elapsed_ms,
            "candidate_ids": b.candidate_ids,
            "job_ids": b.job_ids,
            "job_titles": b.job_titles,
            "candidate_count": b.candidate_count,
            "avg_score": b.avg_score,
            "top_recommendation": b.top_recommendation,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "completed_at": b.completed_at.isoformat() if b.completed_at else None,
            "results": b.results or [],
        }


async def delete_saved_batch(batch_id: str, company_id: str = None) -> bool:
    """Delete a saved batch from DB. Verifies company if provided."""
    from sqlalchemy import delete as sql_delete
    async with AsyncSessionLocal() as db:
        query = sql_delete(BatchAnalysis).where(BatchAnalysis.batch_id == batch_id)
        if company_id:
            query = query.where(BatchAnalysis.company_id == company_id)
        result = await db.execute(query)
        await db.commit()
        return result.rowcount > 0


# ═══════════════════════════════════════════════════════════════════════
# Main batch runner
# ═══════════════════════════════════════════════════════════════════════

async def run_batch_analysis(
    candidate_ids: List[uuid.UUID],
    job_ids: List[uuid.UUID],
    force_reanalyze: bool = False,
    company_id: uuid.UUID = None,
) -> str:
    """
    Launch a batch analysis. Returns a batch_id immediately.
    The actual work runs as a background asyncio task.
    Persists to DB for history access.
    """
    batch_id = str(uuid.uuid4())[:12]
    total_pairs = len(candidate_ids) * len(job_ids)

    # Pre-fetch job titles for display metadata
    job_titles = []
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Job.title).where(Job.id.in_(job_ids))
        )
        job_titles = [row[0] for row in result.all()]

    # Evict old completed batches to prevent unbounded memory growth
    _evict_old_batches()

    state = BatchState(
        batch_id=batch_id,
        total=total_pairs,
        started_at=time.time(),
        candidate_ids=[str(cid) for cid in candidate_ids],
        job_ids=[str(jid) for jid in job_ids],
        job_titles=job_titles,
        company_id=str(company_id) if company_id else "",
    )
    _batch_store[batch_id] = state

    # Persist initial record to DB
    await _create_batch_record(state)

    # Launch the background task with error callback to prevent silent failures
    task = asyncio.create_task(_execute_batch(batch_id, candidate_ids, job_ids, force_reanalyze))

    def _on_batch_done(t: asyncio.Task):
        if t.cancelled():
            logger.warning(f"Batch {batch_id} task was cancelled")
        elif exc := t.exception():
            logger.error(f"Batch {batch_id} task failed with unhandled error: {exc}", exc_info=exc)
            # Mark batch as failed so polling clients get a terminal state
            if batch_id in _batch_store:
                s = _batch_store[batch_id]
                s.status = "failed"
                s.elapsed_ms = int((time.time() - s.started_at) * 1000)
                # Persist failure to DB so it shows in history after restart
                asyncio.ensure_future(_finalize_batch_record(s))

    task.add_done_callback(_on_batch_done)

    logger.info(f"Batch {batch_id} started: {len(candidate_ids)} candidates × {len(job_ids)} jobs = {total_pairs} pairs")
    return batch_id


async def _execute_batch(
    batch_id: str,
    candidate_ids: List[uuid.UUID],
    job_ids: List[uuid.UUID],
    force_reanalyze: bool,
):
    """Background task that processes all (candidate, job) pairs concurrently."""
    state = _batch_store[batch_id]
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    # ── Pre-fetch all candidates and jobs (shared pre-computation) ───
    async with AsyncSessionLocal() as db:
        # Fetch candidates
        result = await db.execute(
            select(Candidate).where(Candidate.id.in_(candidate_ids))
        )
        candidates = {str(c.id): c for c in result.scalars().all()}

        # Fetch jobs
        result = await db.execute(
            select(Job).where(Job.id.in_(job_ids))
        )
        jobs = {str(j.id): j for j in result.scalars().all()}

        # Check existing analyses for dedup (unless force_reanalyze)
        existing_pairs = set()
        existing_analyses = {}
        if not force_reanalyze:
            result = await db.execute(
                select(AnalysisResult).where(
                    AnalysisResult.candidate_id.in_(candidate_ids),
                    AnalysisResult.job_id.in_(job_ids),
                )
            )
            for a in result.scalars().all():
                pair_key = (str(a.candidate_id), str(a.job_id))
                existing_pairs.add(pair_key)
                existing_analyses[pair_key] = a

    # ── Pre-parse job skills (once per job, not once per pair) ──────
    # This avoids redundant LLM calls when the same job is analyzed
    # against multiple candidates. Saves 5-15 seconds per extra candidate.
    # Each job gets its own session so they can run concurrently.
    job_parsed_skills: Dict[str, tuple] = {}  # jid_str -> (required, preferred)

    async def _pre_parse_one_job(jid_str: str):
        from app.api.routes.analysis import _ensure_parsed_skills
        try:
            async with AsyncSessionLocal() as db:
                job_in_session = await db.get(Job, uuid.UUID(jid_str))
                if job_in_session:
                    req, pref = await _ensure_parsed_skills(job_in_session, db)
                    job_parsed_skills[jid_str] = (req, pref)
                    await db.commit()
        except Exception as e:
            logger.warning(f"Pre-parse skills failed for job {jid_str}: {e}")

    # Run all job pre-parses concurrently (not sequentially)
    if jobs:
        await asyncio.gather(
            *[_pre_parse_one_job(jid_str) for jid_str in jobs.keys()],
            return_exceptions=True,
        )

    # ── Build task list ──────────────────────────────────────────────
    tasks = []
    for cid in candidate_ids:
        for jid in job_ids:
            cid_str = str(cid)
            jid_str = str(jid)
            pair_key = (cid_str, jid_str)

            candidate = candidates.get(cid_str)
            job = jobs.get(jid_str)

            if not candidate or not job:
                # Record missing entity error
                item = BatchItemResult(
                    candidate_id=cid_str,
                    candidate_name=candidate.name if candidate else "Unknown",
                    job_id=jid_str,
                    job_title=job.title if job else "Unknown",
                    error="Candidate or job not found",
                )
                state.results.append(item)
                state.failed += 1
                state.completed += 1
                continue

            if pair_key in existing_pairs:
                # Already analyzed — use cached result
                existing = existing_analyses[pair_key]
                item = BatchItemResult(
                    candidate_id=cid_str,
                    candidate_name=candidate.name,
                    job_id=jid_str,
                    job_title=job.title,
                    analysis_id=str(existing.id),
                    overall_score=existing.overall_score,
                    recommendation=existing.recommendation or "",
                    processing_time_ms=existing.processing_time_ms or 0,
                    cached=True,
                )
                state.results.append(item)
                state.cached += 1
                state.completed += 1
                continue

            # Schedule for pipeline execution — pass names/titles for error
            # reporting, but NOT the ORM objects (they're detached from session)
            pre_parsed = job_parsed_skills.get(jid_str)
            tasks.append(_run_single_pair(
                semaphore, state, candidate.name, job.title, cid_str, jid_str,
                pre_parsed_skills=pre_parsed,
            ))

    # ── Execute all pipeline tasks concurrently ─────────────────────
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── Finalize ────────────────────────────────────────────────────
    state.elapsed_ms = int((time.time() - state.started_at) * 1000)
    if state.failed > 0 and state.failed < state.total:
        state.status = "partial_failure"
    elif state.failed == state.total:
        state.status = "failed"
    else:
        state.status = "completed"

    # Sort results by score descending
    state.results.sort(key=lambda r: r.overall_score, reverse=True)

    # Persist final state to DB
    await _finalize_batch_record(state)

    logger.info(
        f"Batch {batch_id} complete: {state.completed}/{state.total} done, "
        f"{state.cached} cached, {state.failed} failed, {state.elapsed_ms}ms total"
    )


async def _run_single_pair(
    semaphore: asyncio.Semaphore,
    state: BatchState,
    candidate_name: str,
    job_title: str,
    cid_str: str,
    jid_str: str,
    pre_parsed_skills: tuple = None,
):
    """Run a single (candidate, job) pipeline under the semaphore."""
    async with semaphore:
        start_time = time.time()
        try:
            # Import scoring functions from analysis route
            from app.api.routes.analysis import (
                _ensure_parsed_skills, _apply_adjacency_boosts,
                _compute_scores, _generate_risk_flags,
                _generate_interview_questions, _generate_summary,
                _sanitize_text,
            )

            async with AsyncSessionLocal() as db:
                # Re-fetch candidate and job INSIDE this session to avoid
                # detached ORM object errors (they were fetched in a different
                # session in _execute_batch and are no longer tracked)
                candidate = await db.get(Candidate, uuid.UUID(cid_str))
                job = await db.get(Job, uuid.UUID(jid_str))
                if not candidate or not job:
                    raise ValueError("Candidate or job not found in DB")

                # Use pre-parsed skills if available (parsed once per job in
                # _execute_batch), otherwise fall back to parsing here
                if pre_parsed_skills:
                    required_skills, preferred_skills = pre_parsed_skills
                else:
                    required_skills, preferred_skills = await _ensure_parsed_skills(job, db)

                resume_parsed = candidate.resume_parsed
                if not resume_parsed:
                    raise ValueError("Candidate resume not parsed")

                # Run skill pipeline
                assessments, pipeline_timings = await skill_pipeline.run(
                    parsed_resume=resume_parsed,
                    required_skills=required_skills,
                    preferred_skills=preferred_skills,
                )

                # Apply adjacency boosts
                _apply_adjacency_boosts(assessments)

                # Delete existing skills for this candidate (avoid duplicates)
                # Must delete evidence first (FK constraint), then skills
                from sqlalchemy import delete as sql_delete
                existing_skill_ids = await db.execute(
                    select(Skill.id).where(Skill.candidate_id == candidate.id)
                )
                skill_id_list = [row[0] for row in existing_skill_ids.all()]
                if skill_id_list:
                    await db.execute(
                        sql_delete(SkillEvidence).where(SkillEvidence.skill_id.in_(skill_id_list))
                    )
                    await db.execute(
                        sql_delete(Skill).where(Skill.id.in_(skill_id_list))
                    )
                    await db.flush()

                # Save skills
                for assessment in [a for a in assessments if a.estimated_depth > 0]:
                    skill_obj = Skill(
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
                    db.add(skill_obj)
                    await db.flush()
                    for ev in assessment.evidence:
                        evidence = SkillEvidence(
                            skill_id=skill_obj.id,
                            evidence_type=ev.evidence_type,
                            description=ev.description,
                            source_text=ev.source_text,
                            strength=ev.strength,
                        )
                        db.add(evidence)

                # Compute scores
                scores = _compute_scores(
                    assessments, required_skills, preferred_skills, resume_parsed,
                    experience_range=job.experience_range,
                    job_title=job.title or "",
                )
                scores["breakdown"]["_pipeline_timings"] = timings_to_dict(pipeline_timings)

                processing_time = int((time.time() - start_time) * 1000)

                # Generate risk flags + interview questions
                risk_flags = _generate_risk_flags(
                    assessments, resume_parsed, scores,
                    experience_range=job.experience_range,
                    job_title=job.title or "",
                )
                interview_questions = _generate_interview_questions(
                    assessments, scores, required_skills, risk_flags,
                    resume_parsed=resume_parsed,
                    candidate_name=candidate.name or "the candidate",
                    job_title=job.title or "this role",
                )

                # Create analysis result
                from app.core.config import settings
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

                # Save risk flags
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

                # Save interview questions
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

                await db.commit()

                # Record success
                item = BatchItemResult(
                    candidate_id=cid_str,
                    candidate_name=candidate.name,
                    job_id=jid_str,
                    job_title=job.title,
                    analysis_id=str(analysis.id),
                    overall_score=scores["overall"],
                    recommendation=scores["recommendation"],
                    processing_time_ms=processing_time,
                )
                state.results.append(item)
                state.completed += 1

                logger.info(
                    f"Batch pair done: {candidate.name} × {job.title} = "
                    f"{scores['overall']:.2f} ({scores['recommendation']}) in {processing_time}ms"
                )

        except Exception as e:
            logger.error(f"Batch pair failed ({cid_str} × {jid_str}): {e}", exc_info=True)
            item = BatchItemResult(
                candidate_id=cid_str,
                candidate_name=candidate_name or "Unknown",
                job_id=jid_str,
                job_title=job_title or "Unknown",
                error=str(e),
            )
            state.results.append(item)
            state.failed += 1
            state.completed += 1
