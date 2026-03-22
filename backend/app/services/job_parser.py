"""
Job requirement parser — extracts structured skill requirements from raw job posting text.
HRs can paste raw career page text and VetLayer intelligently extracts:
  - Required skills with estimated minimum depth (1-5)
  - Preferred/nice-to-have skills
  - Experience range
  - Other metadata

Seniority-aware: job titles like "Senior", "Lead", "Staff", "Principal" automatically
raise minimum depth expectations for core skills.
"""

import re
import logging
from typing import List, Dict, Any

from app.utils.llm_client import llm_client

logger = logging.getLogger(__name__)


# ── Seniority detection and depth floor mapping ─────────────────────
_SENIORITY_PATTERNS = {
    "principal": {"depth_floor": 5, "weight_boost": 0.1},
    "staff": {"depth_floor": 4, "weight_boost": 0.1},
    "senior": {"depth_floor": 4, "weight_boost": 0.05},
    "lead": {"depth_floor": 4, "weight_boost": 0.05},
    "architect": {"depth_floor": 5, "weight_boost": 0.1},
    "sr.": {"depth_floor": 4, "weight_boost": 0.05},
    "sr ": {"depth_floor": 4, "weight_boost": 0.05},
}

_JUNIOR_KEYWORDS = {"junior", "jr.", "jr ", "entry", "associate", "intern", "graduate", "trainee"}


def detect_seniority(title: str, raw_text: str = "") -> dict:
    """
    Detect the seniority level from the job title and description.
    Returns {"level": str, "depth_floor": int, "weight_boost": float}
    """
    title_lower = title.lower().strip() if title else ""
    text_lower = raw_text[:500].lower() if raw_text else ""

    # Check junior first (to avoid "Senior" in description overriding "Junior" in title)
    for kw in _JUNIOR_KEYWORDS:
        if kw in title_lower:
            return {"level": "junior", "depth_floor": 2, "weight_boost": 0.0}

    # Check seniority keywords (title takes priority)
    for kw, config in _SENIORITY_PATTERNS.items():
        if kw in title_lower:
            return {"level": kw.strip(".").strip(), "depth_floor": config["depth_floor"], "weight_boost": config["weight_boost"]}

    # Fallback: check description for seniority signals
    for kw, config in _SENIORITY_PATTERNS.items():
        if kw in text_lower:
            # Lower confidence boost from description alone
            return {"level": kw.strip(".").strip(), "depth_floor": max(3, config["depth_floor"] - 1), "weight_boost": 0.0}

    return {"level": "mid", "depth_floor": 3, "weight_boost": 0.0}


def apply_seniority_boost(skills: list, seniority: dict) -> list:
    """
    Raise min_depth for core skills (weight >= 0.7) based on detected seniority.
    Senior/Lead roles should expect depth 4+ on core skills.
    """
    depth_floor = seniority["depth_floor"]
    weight_boost = seniority["weight_boost"]

    for s in skills:
        weight = s.get("weight", 0.7)
        current_depth = s.get("min_depth", 2)

        # Core skills (weight >= 0.7): enforce depth floor
        if weight >= 0.7 and current_depth < depth_floor:
            s["min_depth"] = depth_floor
            logger.info(
                f"Seniority boost: {s['skill']} min_depth {current_depth} -> {depth_floor} "
                f"(role level: {seniority['level']})"
            )

        # Secondary skills (weight 0.4-0.7): enforce floor minus 1
        elif weight >= 0.4 and current_depth < (depth_floor - 1):
            s["min_depth"] = depth_floor - 1

        # Boost weight slightly for senior roles
        if weight_boost > 0:
            s["weight"] = min(1.0, weight + weight_boost)

    return skills


# ── LLM Prompt ──────────────────────────────────────────────────────

JOB_PARSING_PROMPT = """You are a recruiting intelligence system. Your job is to extract structured skill requirements from raw job posting text.

Given raw text from a job posting (which may be messy, with bullet points, abbreviations, or informal formatting), extract:

1. **required_skills**: Skills that are clearly required or expected. For each:
   - "skill": Canonical skill name (e.g., "React" not "React.js/ReactJS", "Python" not "python programming")
   - "min_depth": Estimated minimum proficiency depth on a 1-5 scale:
     * 1 = Awareness (mentioned, familiarity)
     * 2 = Beginner (basic knowledge, exposure)
     * 3 = Intermediate (hands-on experience, working knowledge)
     * 4 = Advanced (deep experience, can lead/architect)
     * 5 = Expert (industry-recognized mastery)
   - "weight": How important this skill is (0.0-1.0). Core requirements = 0.8-1.0, secondary = 0.5-0.7
   - "category": One of: language, framework, library, database, cloud, devops, testing, tool, concept, data, mobile, ai, general_tool, methodology, security, enterprise, networking, business, strategy

   Use context clues to infer depth:
   - "Knowledge in" / "Familiarity with" → depth 2
   - "Hands-on experience" / "Worked with" → depth 3
   - "Strong experience" / "Deep expertise" → depth 4
   - "Expert" / "Mastery" / "Led architecture of" → depth 5
   - Years of experience hint at depth: 1-2y → depth 3, 3-5y → depth 4, 5+y → depth 4-5

2. **preferred_skills**: Nice-to-have skills. Same format but these are optional.

3. **experience_range**: If mentioned, extract {"min_years": X, "max_years": Y}. Use null for either if not specified.

4. **title_suggestion**: If the text doesn't clearly state a job title, suggest one based on the requirements.

Return JSON:
{
  "required_skills": [{"skill": "...", "min_depth": N, "weight": N.N, "category": "..."}, ...],
  "preferred_skills": [{"skill": "...", "min_depth": N, "weight": N.N, "category": "..."}, ...],
  "experience_range": {"min_years": N, "max_years": N} or null,
  "title_suggestion": "..." or null
}

Be smart about grouping related technologies. For example:
- "HTML, CSS, SASS" → separate skills: HTML (depth 3), CSS (depth 3), SASS (depth 3)
- "Object Oriented Javascript" → JavaScript (depth 3)
- "SPA Framework patterns" → this is about React/Vue/Angular architecture, infer from context
- "Caching / Storage / Compatibility" → Browser APIs (depth 2)
- "module bundlers like Webpack" → Webpack (depth 2)

CRITICAL — EXCLUDE generic soft skills and personality traits.
EXCLUDE things like: communication (generic), teamwork, collaboration (generic), problem solving (generic),
time management, attention to detail, critical thinking (generic), self-motivated, fast learner, passionate,
creative thinking (generic), adaptability, interpersonal skills, multitasking, work ethic, initiative.

INCLUDE: programming languages, frameworks, libraries, databases, cloud platforms, DevOps tools,
protocols, APIs, testing frameworks, data tools, design tools, specific methodologies (Agile/Scrum are borderline
 — only include if the posting heavily emphasizes it as a tooling/process skill), and other concrete technical skills
that can be verified on a resume.

ALSO INCLUDE domain-specific professional competencies for business/strategy/operations roles.
These are NOT soft skills — they are assessable domain skills with verifiable evidence on resumes:
- "Client Experience Strategy" / "Customer Experience" / "CX Strategy" → category "business"
- "Account Management" / "Key Account Management" → category "business"
- "Business Development" / "Sales Strategy" → category "business"
- "Experience Design" / "Service Design" → category "strategy"
- "Operational Excellence" / "Process Improvement" / "Six Sigma" / "Lean" → category "methodology"
- "Stakeholder Engagement" (when it's a core accountability, not just a trait) → category "business"
- "Program Management" / "Portfolio Management" (when it's a core role function) → category "methodology"
- "Executive Storytelling" / "Executive Facilitation" → category "strategy"
- "Team Leadership" / "People Development" (when hiring/coaching/building teams is a core accountability) → category "business"
- "Strategic Planning" / "Go-to-Market Strategy" (when it's a core function of the role) → category "strategy"
- "Vendor Management" / "Partner Management" → category "business"
- "Change Management" (when it's a structured discipline, not just adaptability) → category "methodology"
- "Governance" / "Compliance" / "Risk Management" → category "business"

The distinction: if a job posting lists a competency as a CORE ACCOUNTABILITY with specific expectations
(e.g., "Own end-to-end governance for client visits"), it's an assessable domain skill.
If it's listed as a generic nice-to-have trait (e.g., "strong communication skills"), it's a soft skill to exclude.

ALSO INCLUDE general professional tools when explicitly listed in the job posting:
- "Microsoft Office" / "MS Office" / "Office 365" → include as skill with category "general_tool"
- "Google Workspace" / "G Suite" → include as skill with category "general_tool"
- "AI tools" / "AI-powered tools" / "Copilot" / "ChatGPT" → include as skill with category "ai"
- "Adobe Creative Suite" / "Photoshop" / "Illustrator" → include as skill with category "tool"
- "Figma" / "Sketch" → include as skill with category "tool"
These are assessable: candidates either have verifiable experience with them or they don't.

ALSO INCLUDE methodologies and project management tools when explicitly listed:
- "Agile" / "Scrum" / "Kanban" / "SAFe" → include with category "methodology"
- "Jira" / "Asana" / "Monday" / "Trello" / "Linear" / "ClickUp" → include with category "tool"
- "Confluence" / "Notion" → include with category "tool"
These are assessable process skills: candidates either have verifiable experience using them or they don't.

ALSO INCLUDE enterprise/CRM/security tools when explicitly listed:
- "Salesforce" / "SAP" / "Oracle ERP" / "ServiceNow" / "Workday" → include with category "enterprise"
- "Splunk" / "SIEM" / "Penetration Testing" / "SOC2" / "CISSP" → include with category "security"
- "Cisco" / "Networking" / "Load Balancing" / "DNS" → include with category "networking"

The reason for excluding soft skills: VetLayer assesses skills by looking for evidence on resumes. Soft skills
are rarely listed with concrete evidence, making them unreliable to assess and unfairly penalizing candidates.

Keep skill names clean and canonical — these will be matched against candidate resumes."""


# ── Soft skill blocklist — deterministic safety net ────────────────
# LLMs don't always follow instructions, so we strip these post-parse.
# Normalized to lowercase for matching.
_SOFT_SKILL_BLOCKLIST = {
    # Communication & interpersonal (generic traits)
    "communication", "communication skills", "verbal communication",
    "written communication", "interpersonal skills", "interpersonal",
    "public speaking",
    "negotiation skills", "conflict resolution",
    # Teamwork & collaboration (generic traits)
    "teamwork", "team player", "team work", "collaboration",
    "cross-functional collaboration", "cross functional",
    "working with others", "cooperative",
    # Generic leadership traits (NOT domain-specific leadership roles)
    "leadership", "leadership skills",
    "mentoring", "mentorship", "coaching", "delegation",
    "decision making", "decision-making",
    # Problem solving & thinking (generic)
    "problem solving", "problem-solving", "critical thinking",
    "analytical thinking", "analytical skills", "creative thinking",
    "creativity", "innovative thinking",
    "troubleshooting",  # borderline — too vague without a domain
    # Work habits & personality
    "attention to detail", "detail oriented", "detail-oriented",
    "time management", "organizational skills", "organization",
    "multitasking", "multi-tasking", "prioritization",
    "self-motivated", "self motivated", "self-starter",
    "fast learner", "quick learner", "eager to learn",
    "adaptability", "flexibility", "resilience",
    "work ethic", "initiative", "proactive", "proactiveness",
    "passionate", "passion", "enthusiasm", "motivated",
    "accountable", "accountability", "ownership mentality",
    "dependable", "reliable", "reliability",
    # Generic non-technical
    "emotional intelligence", "empathy", "patience",
    "cultural awareness", "diversity", "inclusion",
    "research", "research skills",
}
# NOTE: The following are intentionally NOT in the blocklist because they are
# assessable domain skills when they appear as core accountabilities in a JD:
# - stakeholder management/engagement, client management, relationship management
# - people management, team management, team leadership, team building
# - strategic thinking, strategic planning
# - project management, program management, resource management
# - change management, business acumen, innovation
# - customer service, customer support, customer focus
# - presentation skills, negotiation
# - documentation (could be a tool skill)
# The LLM prompt instructs to only include these when they are core accountabilities,
# and the category system (business/strategy) helps distinguish them from soft skills.


def _is_soft_skill(skill_name: str) -> bool:
    """Check if a skill name is a soft/non-technical skill."""
    name = skill_name.lower().strip()
    # Exact match
    if name in _SOFT_SKILL_BLOCKLIST:
        return True
    # Fuzzy: check if any blocklist term is the entire skill
    # (avoids false positives like "REST API documentation")
    return False


async def parse_job_requirements(raw_text: str, job_title: str = "") -> Dict[str, Any]:
    """
    Parse raw job posting text into structured skill requirements.
    Applies seniority-aware depth boosting based on job title.
    Filters out non-technical/soft skills that can't be reliably assessed from resumes.
    Returns dict with required_skills, preferred_skills, experience_range, title_suggestion, seniority.
    """
    if not raw_text or len(raw_text.strip()) < 10:
        return {
            "required_skills": [],
            "preferred_skills": [],
            "experience_range": None,
            "title_suggestion": None,
            "seniority": detect_seniority(job_title, ""),
        }

    logger.info(f"Parsing job requirements from {len(raw_text)} chars of raw text")

    result = await llm_client.complete_json(
        system_prompt=JOB_PARSING_PROMPT,
        user_message=f"Extract skill requirements from this job posting text:\n\n{raw_text[:5000]}",
        max_tokens=3000,
    )

    required = result.get("required_skills", [])
    preferred = result.get("preferred_skills", [])

    # Validate and normalize
    valid_categories = {
        "language", "framework", "library", "database", "cloud",
        "devops", "testing", "tool", "concept", "data", "mobile",
        "ai", "general_tool", "methodology", "security", "enterprise",
        "networking", "business", "strategy", "unknown",
    }
    def _safe_int(val, default):
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def _safe_float(val, default):
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    for skill_list in [required, preferred]:
        for s in skill_list:
            s["skill"] = str(s.get("skill", "")).strip()
            s["min_depth"] = max(1, min(5, _safe_int(s.get("min_depth", 2), 2)))
            s["weight"] = max(0.1, min(1.0, _safe_float(s.get("weight", 0.7), 0.7)))
            cat = str(s.get("category", "unknown")).strip().lower()
            s["category"] = cat if cat in valid_categories else "unknown"

    # Remove empty entries
    required = [s for s in required if s["skill"]]
    preferred = [s for s in preferred if s["skill"]]

    # ── Filter out soft skills (safety net — LLMs sometimes ignore instructions)
    before_req = len(required)
    before_pref = len(preferred)
    required = [s for s in required if not _is_soft_skill(s["skill"])]
    preferred = [s for s in preferred if not _is_soft_skill(s["skill"])]
    filtered_count = (before_req - len(required)) + (before_pref - len(preferred))
    if filtered_count > 0:
        logger.info(f"Filtered {filtered_count} soft/non-technical skill(s) from parsed requirements")

    # ── Seniority-aware depth boosting ──────────────────────────────
    inferred_title = job_title or result.get("title_suggestion", "")
    seniority = detect_seniority(inferred_title, raw_text)
    logger.info(f"Detected seniority: {seniority['level']} (depth floor: {seniority['depth_floor']})")

    required = apply_seniority_boost(required, seniority)

    logger.info(f"Extracted {len(required)} required + {len(preferred)} preferred skills")

    return {
        "required_skills": required,
        "preferred_skills": preferred,
        "experience_range": result.get("experience_range"),
        "title_suggestion": result.get("title_suggestion"),
        "seniority": seniority,
    }


def get_role_suggested_skills(title: str) -> list:
    """
    Get suggested skills based on job title alone.
    Used when a recruiter creates a job with just a title and no detailed requirements.
    Imports from analysis.py's role skill stack mapping.
    """
    try:
        from app.api.routes.analysis import get_role_skill_stack
        return get_role_skill_stack(title)
    except ImportError:
        return []
