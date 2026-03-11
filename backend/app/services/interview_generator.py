"""
Interview Focus Generator
Creates targeted interview questions based on analysis results.

Now implemented directly in app/api/routes/analysis.py via _generate_interview_questions().
This file is kept for the GeneratedQuestion dataclass reference.
"""

from typing import List, Optional
from dataclasses import dataclass, field


@dataclass
class GeneratedQuestion:
    """A generated interview question."""
    category: str               # skill_verification, gap_exploration, depth_probe, behavioral, red_flag
    question: str
    rationale: str              # Why this question was generated
    target_skill: Optional[str] = None
    expected_depth: Optional[int] = None
    priority: int = 5           # 1 = highest priority
    follow_ups: List[str] = field(default_factory=list)
