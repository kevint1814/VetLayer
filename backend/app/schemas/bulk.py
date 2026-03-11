"""
Pydantic schemas for bulk operations.
"""

import uuid
from typing import List, Dict, Optional
from pydantic import BaseModel


class BulkDeleteRequest(BaseModel):
    ids: List[uuid.UUID]


class BulkDeleteResponse(BaseModel):
    deleted_count: int
    failed_ids: List[str] = []
    errors: Dict[str, str] = {}


# ── Batch Analysis ──────────────────────────────────────────────────

class BatchAnalysisRequest(BaseModel):
    """Kick off batch analysis: N candidates × M jobs."""
    candidate_ids: List[uuid.UUID]
    job_ids: List[uuid.UUID]
    force_reanalyze: bool = False


class BatchItemResult(BaseModel):
    """One (candidate, job) pair result inside a batch."""
    candidate_id: str
    candidate_name: str
    job_id: str
    job_title: str
    analysis_id: str
    overall_score: float
    recommendation: str
    processing_time_ms: Optional[int] = None
    cached: bool = False
    error: Optional[str] = None


class BatchAnalysisStatus(BaseModel):
    """Progress and results of a running or completed batch."""
    batch_id: str
    status: str  # "processing", "completed", "partial_failure"
    total: int
    completed: int
    failed: int
    cached: int
    results: List[BatchItemResult] = []
    elapsed_ms: Optional[int] = None
