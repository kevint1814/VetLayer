"""
Experience Trajectory Scorer — Analyzes career progression as a scoring dimension.

For non-tech and hybrid roles, career trajectory is often the strongest signal:
  - A consistent upward path (Associate → Manager → Director) signals growth
  - Lateral moves across domains signal breadth
  - Gaps, demotions, or stagnation signal risk

This module scores career progression independently of skill assessment,
providing a trajectory_score (0-100) that feeds into the adaptive scoring engine.
"""

import re
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Seniority Level Mapping (title → numeric level)
# ═══════════════════════════════════════════════════════════════════════

_SENIORITY_LEVELS = {
    # Entry level (1-2)
    "intern": 1, "trainee": 1, "apprentice": 1,
    "junior": 2, "associate": 2, "entry": 2, "graduate": 2,
    "assistant": 2, "coordinator": 2, "analyst": 2.5,
    # Mid level (3)
    "mid": 3, "specialist": 3, "consultant": 3, "engineer": 3,
    "developer": 3, "designer": 3, "executive": 3,
    # Senior level (4)
    "senior": 4, "sr": 4, "lead": 4, "team lead": 4,
    "supervisor": 4, "principal": 4.5, "staff": 4.5,
    # Management (5)
    "manager": 5, "head": 5, "architect": 5,
    "engineering manager": 5, "program manager": 5,
    # Finance/Professional management (5-6)
    "controller": 5.5, "comptroller": 5.5,
    "financial controller": 6, "finance controller": 6, "group controller": 6,
    "treasurer": 5.5, "company secretary": 5,
    "audit manager": 5, "risk manager": 5, "compliance manager": 5,
    # Director level (6)
    "director": 6, "senior manager": 5.5, "general manager": 6,
    "senior director": 6.5,
    "associate director": 5.5, "assistant director": 5.5,
    # VP level (7)
    "vp": 7, "vice president": 7, "avp": 6.5,
    "svp": 7.5, "senior vice president": 7.5,
    # C-level (8)
    "cto": 8, "cfo": 8, "coo": 8, "ceo": 8, "cio": 8,
    "ciso": 8, "chro": 8, "cmo": 8, "cro": 8,
    "chief": 8, "partner": 7.5, "founder": 7.5, "co-founder": 7.5,
    "president": 8,
    # Consulting/Professional services
    "managing director": 7.5, "practice lead": 5.5,
    "engagement manager": 5, "delivery manager": 5,
}

# Patterns that modify seniority
_SENIORITY_MODIFIERS = [
    (r"\bchief\s+\w+\s+officer\b", 8),
    (r"\bvice\s+president\b", 7),
    (r"\bsenior\s+vice\s+president\b", 7.5),
    (r"\bsenior\s+director\b", 6.5),
    (r"\bsenior\s+manager\b", 5.5),
    (r"\bassistant\s+vice\s+president\b", 6.5),
    (r"\bengineering\s+manager\b", 5),
    (r"\bteam\s+lead\b", 4),
    (r"\btech\s*lead\b", 4),
    (r"\btechnical\s+lead\b", 4),
    # Finance/Professional compound titles
    (r"\bfinancial\s+controller\b", 6),
    (r"\bfinance\s+controller\b", 6),
    (r"\bgroup\s+controller\b", 6),
    (r"\bregional\s+controller\b", 5.5),
    (r"\bhead\s+of\s+finance\b", 6),
    (r"\bhead\s+of\s+treasury\b", 6),
    (r"\bhead\s+of\s+compliance\b", 6),
    (r"\bhead\s+of\s+risk\b", 6),
    (r"\bhead\s+of\s+audit\b", 6),
    (r"\bhead\s+of\s+\w+\b", 5),
    (r"\bmanaging\s+director\b", 7.5),
    (r"\bassociate\s+director\b", 5.5),
    (r"\bpractice\s+(lead|head|director)\b", 5.5),
    (r"\bengagement\s+(manager|director)\b", 5),
    (r"\bdelivery\s+(manager|director|head)\b", 5),
]

# ═══════════════════════════════════════════════════════════════════════
# Industry classification
# ═══════════════════════════════════════════════════════════════════════

_INDUSTRY_KEYWORDS = {
    "technology": ["software", "saas", "tech", "technology", "platform", "startup", "app", "digital"],
    "finance": ["bank", "finance", "financial", "investment", "insurance", "fintech", "capital", "wealth"],
    "healthcare": ["hospital", "health", "medical", "pharma", "pharmaceutical", "biotech", "clinical"],
    "consulting": ["consulting", "advisory", "consultancy", "deloitte", "mckinsey", "accenture", "pwc", "kpmg", "ey", "ernst"],
    "retail": ["retail", "e-commerce", "ecommerce", "store", "consumer", "brand", "fashion"],
    "manufacturing": ["manufacturing", "factory", "production", "industrial", "automotive"],
    "media": ["media", "publishing", "advertising", "agency", "creative", "entertainment", "content"],
    "telecom": ["telecom", "telecommunications", "carrier", "network operator"],
    "education": ["university", "school", "education", "academic", "college", "edtech"],
    "government": ["government", "public sector", "federal", "state agency", "municipality"],
    "real_estate": ["real estate", "property", "construction", "architecture", "building"],
    "energy": ["energy", "oil", "gas", "renewable", "solar", "wind", "utility"],
    "logistics": ["logistics", "shipping", "freight", "supply chain", "warehouse", "distribution"],
}


def analyze_trajectory(
    parsed_resume: dict,
    target_job_title: str = "",
    target_industry: str = "",
) -> Dict[str, Any]:
    """
    Analyze a candidate's career trajectory from their parsed resume.

    Returns:
    {
        "trajectory_score": 0-100,
        "growth_rate": float,       # Seniority levels gained per year
        "total_years": float,
        "gap_months": int,          # Total gap months
        "gap_count": int,           # Number of gaps
        "progression_type": str,    # "ascending" | "lateral" | "descending" | "mixed" | "early_career"
        "current_seniority": float, # Current estimated seniority level
        "peak_seniority": float,    # Highest seniority reached
        "industry_consistency": float,  # 0-1 how consistent their industry is
        "industry_match": float,    # 0-1 how well their industry matches the target
        "company_tier_score": float,# 0-1 quality signal from company names
        "trajectory_summary": str,  # Human-readable summary
    }
    """
    experiences = parsed_resume.get("experience") or []

    if not experiences:
        return _empty_trajectory("No work experience found on resume.")

    # ── Parse and sort experiences ─────────────────────────────────────
    parsed_exps = _parse_experiences(experiences)

    if not parsed_exps:
        return _empty_trajectory("Could not parse experience dates from resume.")

    # Sort by start date (most recent first for current detection, oldest first for trajectory)
    parsed_exps.sort(key=lambda x: x["start_months"], reverse=False)

    # ── Compute trajectory metrics ─────────────────────────────────────
    total_years = _compute_total_years(parsed_exps)
    current_seniority = _get_seniority_level(parsed_exps[-1]["title"])
    peak_seniority = max(_get_seniority_level(exp["title"]) for exp in parsed_exps)

    # Growth rate: seniority change per year
    if len(parsed_exps) >= 2 and total_years > 0:
        first_level = _get_seniority_level(parsed_exps[0]["title"])
        growth_rate = (current_seniority - first_level) / max(total_years, 1)
    else:
        growth_rate = 0.0

    # Progression type
    progression_type = _classify_progression(parsed_exps)

    # Gap analysis
    gap_months, gap_count = _analyze_gaps(parsed_exps)

    # Industry analysis
    industries = [_detect_industry(exp) for exp in parsed_exps]
    industry_consistency = _compute_industry_consistency(industries)
    industry_match = _compute_industry_match(industries, target_industry, target_job_title)

    # Company tier/quality score
    company_tier = _estimate_company_tier(parsed_exps)

    # ── Compute overall trajectory score ───────────────────────────────
    trajectory_score = _compute_trajectory_score(
        growth_rate=growth_rate,
        total_years=total_years,
        current_seniority=current_seniority,
        peak_seniority=peak_seniority,
        progression_type=progression_type,
        gap_months=gap_months,
        gap_count=gap_count,
        industry_consistency=industry_consistency,
        industry_match=industry_match,
        company_tier=company_tier,
        num_roles=len(parsed_exps),
    )

    # ── Build summary ──────────────────────────────────────────────────
    summary = _build_trajectory_summary(
        parsed_exps, progression_type, growth_rate, gap_count,
        current_seniority, total_years, industry_match
    )

    result = {
        "trajectory_score": round(trajectory_score),
        "growth_rate": round(growth_rate, 3),
        "total_years": round(total_years, 1),
        "gap_months": gap_months,
        "gap_count": gap_count,
        "progression_type": progression_type,
        "current_seniority": round(current_seniority, 1),
        "peak_seniority": round(peak_seniority, 1),
        "industry_consistency": round(industry_consistency, 3),
        "industry_match": round(industry_match, 3),
        "company_tier_score": round(company_tier, 3),
        "trajectory_summary": summary,
        "role_count": len(parsed_exps),
    }

    logger.info(
        f"Trajectory analysis: score={trajectory_score}, type={progression_type}, "
        f"growth_rate={growth_rate:.3f}, years={total_years:.1f}, "
        f"seniority={current_seniority:.1f}, gaps={gap_count}"
    )

    return result


# ═══════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════

def _empty_trajectory(reason: str) -> Dict[str, Any]:
    return {
        "trajectory_score": 0,
        "growth_rate": 0.0,
        "total_years": 0.0,
        "gap_months": 0,
        "gap_count": 0,
        "progression_type": "unknown",
        "current_seniority": 0.0,
        "peak_seniority": 0.0,
        "industry_consistency": 0.0,
        "industry_match": 0.0,
        "company_tier_score": 0.0,
        "trajectory_summary": reason,
        "role_count": 0,
    }


def _parse_experiences(experiences: list) -> list:
    """Parse raw experience entries into structured records with month-level dates."""
    parsed = []
    current_year = datetime.now().year
    current_month = datetime.now().month

    for exp in experiences:
        title = exp.get("title") or exp.get("role") or ""
        company = exp.get("company") or ""
        start_date = str(exp.get("start_date") or "")
        end_date = str(exp.get("end_date") or "")
        description = exp.get("description") or ""

        start_m = _date_to_months(start_date)
        if end_date.lower() in ("present", "current", "now", "") or not end_date:
            end_m = current_year * 12 + current_month
        else:
            end_m = _date_to_months(end_date)

        if start_m and end_m and end_m >= start_m:
            parsed.append({
                "title": title,
                "company": company,
                "start_months": start_m,
                "end_months": end_m,
                "duration_months": end_m - start_m,
                "description": description,
            })

    return parsed


def _date_to_months(date_str: str) -> Optional[int]:
    """Convert a date string to absolute months (year * 12 + month)."""
    if not date_str:
        return None

    # Try "Month Year" format (e.g., "Jan 2020", "January 2020")
    month_match = re.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*(\d{4})', date_str.lower())
    if month_match:
        month_abbr = month_match.group(1)[:3]
        year = int(month_match.group(2))
        months_map = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                      "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
        month = months_map.get(month_abbr, 6)
        return year * 12 + month

    # Try "YYYY-MM" or "YYYY/MM" (ISO format — common from resume parsers)
    iso_match = re.search(r'((?:20|19)\d{2})[/-](\d{1,2})(?:\b|$)', date_str)
    if iso_match:
        year = int(iso_match.group(1))
        month = int(iso_match.group(2))
        if 1 <= month <= 12:
            return year * 12 + month

    # Try "MM/YYYY" or "MM-YYYY"
    date_match = re.search(r'(\d{1,2})[/-](\d{4})', date_str)
    if date_match:
        month = int(date_match.group(1))
        year = int(date_match.group(2))
        if 1 <= month <= 12:
            return year * 12 + month

    # Try just year
    year_match = re.search(r'(20\d{2}|19\d{2})', date_str)
    if year_match:
        year = int(year_match.group(1))
        return year * 12 + 6  # Assume mid-year

    return None


def _get_seniority_level(title: str) -> float:
    """Map a job title to a numeric seniority level (1-8)."""
    title_lower = title.lower().strip()

    # Check compound patterns first (more specific)
    for pattern, level in _SENIORITY_MODIFIERS:
        if re.search(pattern, title_lower):
            return level

    # Priority order: check highest-level keywords first, then lower ones.
    # Also check entry-level/junior keywords BEFORE generic ones like "engineer".
    # Phase 1: Check explicit low-level indicators
    low_level_keywords = {"intern": 1, "trainee": 1, "apprentice": 1,
                          "junior": 2, "entry": 2, "graduate": 2,
                          "assistant": 2}
    for keyword, level in low_level_keywords.items():
        if keyword in title_lower:
            return level

    # Phase 2: Check high-level keywords (take highest match)
    # Sort keywords by level descending so we can break early on highest match
    # Use word boundary matching to avoid "cto" matching inside "director"
    high_level_keywords = {k: v for k, v in _SENIORITY_LEVELS.items()
                           if v >= 4 and k not in low_level_keywords}
    for keyword, level in sorted(high_level_keywords.items(), key=lambda x: -x[1]):
        if re.search(r'\b' + re.escape(keyword) + r'\b', title_lower):
            return level

    # Phase 3: Check below-mid keywords (associate, analyst, coordinator)
    # These are level 2-3, so we check them and return the first match
    # rather than using max() against the 3.0 default (which swallows 2.5 values)
    below_mid_keywords = {"associate": 2.5, "coordinator": 2.5, "analyst": 2.5}
    for keyword, level in below_mid_keywords.items():
        if keyword in title_lower:
            return level

    # Phase 4: Check exact-mid keywords (specialist, consultant, executive)
    mid_keywords = {"specialist": 3, "consultant": 3, "executive": 3}
    for keyword, level in mid_keywords.items():
        if keyword in title_lower:
            return level

    return 3.0  # Default to mid-level


def _compute_total_years(parsed_exps: list) -> float:
    """Compute total years using interval merging (handles overlapping roles)."""
    if not parsed_exps:
        return 0.0

    intervals = [(exp["start_months"], exp["end_months"]) for exp in parsed_exps]
    intervals.sort(key=lambda x: x[0])

    merged = [intervals[0]]
    for start, end in intervals[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    total_months = sum(end - start for start, end in merged)
    return total_months / 12.0


def _classify_progression(parsed_exps: list) -> str:
    """Classify the career progression pattern."""
    if len(parsed_exps) <= 1:
        return "early_career"

    levels = [_get_seniority_level(exp["title"]) for exp in parsed_exps]

    # Count transitions
    ups = 0
    downs = 0
    laterals = 0

    for i in range(1, len(levels)):
        diff = levels[i] - levels[i - 1]
        if diff > 0.3:
            ups += 1
        elif diff < -0.3:
            downs += 1
        else:
            laterals += 1

    total = ups + downs + laterals
    if total == 0:
        return "early_career"

    if ups > 0 and downs == 0:
        return "ascending"
    elif downs > 0 and ups == 0:
        return "descending"
    elif laterals > (ups + downs):
        return "lateral"
    elif ups > downs:
        return "ascending"
    else:
        return "mixed"


def _analyze_gaps(parsed_exps: list) -> Tuple[int, int]:
    """Analyze employment gaps. Returns (total_gap_months, gap_count)."""
    if len(parsed_exps) < 2:
        return 0, 0

    sorted_exps = sorted(parsed_exps, key=lambda x: x["start_months"])
    total_gap = 0
    gap_count = 0

    for i in range(len(sorted_exps) - 1):
        end_current = sorted_exps[i]["end_months"]
        start_next = sorted_exps[i + 1]["start_months"]
        gap = start_next - end_current

        if gap > 3:  # Only count gaps > 3 months
            total_gap += gap
            gap_count += 1

    return total_gap, gap_count


def _detect_industry(exp: dict) -> str:
    """Detect the industry of a single experience entry."""
    text = f"{exp.get('company', '')} {exp.get('title', '')} {exp.get('description', '')}".lower()

    best_match = "unknown"
    best_score = 0

    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score = score
            best_match = industry

    return best_match


def _compute_industry_consistency(industries: list) -> float:
    """How consistent is the candidate's industry across roles? 0-1."""
    if not industries:
        return 0.0

    # Filter out "unknown"
    known = [i for i in industries if i != "unknown"]
    if not known:
        return 0.5  # Can't tell, give benefit of doubt

    from collections import Counter
    counts = Counter(known)
    most_common_count = counts.most_common(1)[0][1]
    return most_common_count / len(known)


def _compute_industry_match(industries: list, target_industry: str, target_title: str) -> float:
    """How well does the candidate's industry background match the target role?"""
    if not industries or (not target_industry and not target_title):
        return 0.5  # Unknown, neutral

    # Detect target industry from title/industry
    target_text = f"{target_industry} {target_title}".lower()
    target_ind = "unknown"
    best_score = 0
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in target_text)
        if score > best_score:
            best_score = score
            target_ind = industry

    if target_ind == "unknown":
        return 0.5

    # Count how many roles match the target industry
    known = [i for i in industries if i != "unknown"]
    if not known:
        return 0.5

    match_count = sum(1 for i in known if i == target_ind)
    return match_count / len(known)


def _estimate_company_tier(parsed_exps: list) -> float:
    """
    Rough company quality signal. Look for signals like:
    - Fortune 500 / well-known companies
    - Startup indicators
    - MNC / global indicators
    """
    # Large/known company indicators
    quality_signals = [
        "fortune", "inc.", "corporation", "global", "international",
        "enterprise", "technologies", "solutions", "consulting",
    ]
    # Well-known companies (partial list)
    tier1_companies = {
        "google", "amazon", "microsoft", "apple", "meta", "netflix",
        "uber", "airbnb", "stripe", "salesforce", "oracle", "ibm",
        "deloitte", "mckinsey", "accenture", "pwc", "kpmg", "ey",
        "goldman", "morgan stanley", "jp morgan", "jpmorgan",
        "wipro", "tcs", "infosys", "cognizant", "hcl", "tech mahindra",
    }

    score = 0.5  # Default
    for exp in parsed_exps:
        company_lower = exp.get("company", "").lower()
        # Check tier 1
        for t1 in tier1_companies:
            if t1 in company_lower:
                score = max(score, 0.85)
                break
        # Check quality signals
        for qs in quality_signals:
            if qs in company_lower:
                score = max(score, 0.65)
                break

    return score


def _compute_trajectory_score(
    growth_rate: float,
    total_years: float,
    current_seniority: float,
    peak_seniority: float,
    progression_type: str,
    gap_months: int,
    gap_count: int,
    industry_consistency: float,
    industry_match: float,
    company_tier: float,
    num_roles: int,
) -> float:
    """
    Compute a 0-100 trajectory score from all trajectory signals.

    Component weights:
    - Progression pattern: 25%
    - Growth rate: 20%
    - Seniority level: 15%
    - Industry signals: 15%
    - Gap penalty: -10% (negative)
    - Company quality: 10%
    - Career stability: 5%
    """
    # Progression pattern score (0-25)
    progression_scores = {
        "ascending": 25, "lateral": 15, "mixed": 10,
        "descending": 5, "early_career": 12, "unknown": 10,
    }
    progression_component = progression_scores.get(progression_type, 10)

    # Growth rate score (0-20)
    # Good growth: 0.3-0.5 levels per year (e.g., junior to senior in 6-10 years)
    if growth_rate >= 0.5:
        growth_component = 20
    elif growth_rate >= 0.3:
        growth_component = 17
    elif growth_rate >= 0.15:
        growth_component = 13
    elif growth_rate >= 0.05:
        growth_component = 8
    elif growth_rate >= 0:
        growth_component = 5
    else:
        growth_component = 2  # Negative growth (demotion)

    # Seniority level score (0-15)
    seniority_component = min(current_seniority / 8.0 * 15, 15)

    # Industry signals (0-15)
    industry_component = (industry_consistency * 7.5 + industry_match * 7.5)

    # Gap penalty (0 to -10)
    gap_penalty = min(gap_months * 0.5 + gap_count * 1.5, 10)

    # Company quality (0-10)
    company_component = company_tier * 10

    # Career stability (0-5): penalize too many short stints
    if num_roles > 0:
        avg_tenure_months = (total_years * 12) / num_roles
        if avg_tenure_months >= 24:
            stability = 5
        elif avg_tenure_months >= 18:
            stability = 4
        elif avg_tenure_months >= 12:
            stability = 3
        else:
            stability = 1  # Job hopper signal
    else:
        stability = 0

    total = (
        progression_component +
        growth_component +
        seniority_component +
        industry_component -
        gap_penalty +
        company_component +
        stability
    )

    return max(0, min(100, total))


def _build_trajectory_summary(
    parsed_exps: list,
    progression_type: str,
    growth_rate: float,
    gap_count: int,
    current_seniority: float,
    total_years: float,
    industry_match: float,
) -> str:
    """Build a human-readable trajectory summary."""
    parts = []

    # Career span
    years_str = f"{total_years:.0f}" if total_years == int(total_years) else f"{total_years:.1f}"
    parts.append(f"{years_str} years of professional experience across {len(parsed_exps)} roles")

    # Progression
    prog_descriptions = {
        "ascending": "showing consistent upward career progression",
        "lateral": "with lateral moves across different functions or domains",
        "descending": "with a downward seniority trend",
        "mixed": "with a mix of upward and lateral moves",
        "early_career": "in early career stage",
    }
    if progression_type in prog_descriptions:
        parts.append(prog_descriptions[progression_type])

    # Current level
    level_names = {
        1: "entry level", 2: "junior", 3: "mid level", 4: "senior",
        5: "management", 6: "director level", 7: "VP level", 8: "C level",
    }
    level_name = level_names.get(round(current_seniority), "mid level")
    parts.append(f"currently at {level_name}")

    # Gaps
    if gap_count > 0:
        parts.append(f"with {gap_count} career gap{'s' if gap_count > 1 else ''}")

    # Industry match
    if industry_match >= 0.7:
        parts.append("strong industry alignment with target role")
    elif industry_match >= 0.4:
        parts.append("moderate industry overlap with target role")

    return ". ".join(parts) + "."
