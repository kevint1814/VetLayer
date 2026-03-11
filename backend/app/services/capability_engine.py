"""
Capability Engine
Scores a candidate's skill assessments against job requirements.

Phase 3 implementation target.
"""

from typing import List, Optional
from dataclasses import dataclass, field


@dataclass
class SkillMatch:
    """Match result for a single required skill."""
    skill_name: str
    required_depth: int
    candidate_depth: int
    is_match: bool
    gap: int                    # Positive = exceeds, negative = falls short
    weight: float               # How important this skill is for the job
    weighted_score: float


@dataclass
class CapabilityScore:
    """Overall capability assessment."""
    overall_score: float                    # 0.0 - 1.0
    skill_match_score: float
    experience_score: float
    education_score: float
    depth_weighted_score: float
    skill_matches: List[SkillMatch] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)


class CapabilityEngine:
    """
    Compares candidate skill assessments against job requirements.
    Produces a weighted score and detailed breakdown.
    """

    async def score(self, skill_assessments: list, job_requirements: dict) -> CapabilityScore:
        """Score a candidate against job requirements."""
        # TODO: Phase 3
        raise NotImplementedError("Capability engine — Phase 3")
