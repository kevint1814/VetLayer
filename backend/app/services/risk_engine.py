"""
Risk Engine
Identifies red flags, inconsistencies, and areas of concern.

Now implemented directly in app/api/routes/analysis.py via _generate_risk_flags().
This file is kept for the RiskFlagResult dataclass reference.
"""

from typing import List
from dataclasses import dataclass


@dataclass
class RiskFlagResult:
    """A single risk flag."""
    flag_type: str              # employment_gap, skill_inflation, inconsistent_timeline, etc.
    severity: str               # low, medium, high, critical
    title: str
    description: str
    evidence: str
    suggestion: str             # What the recruiter should ask about
