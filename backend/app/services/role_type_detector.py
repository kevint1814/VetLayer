"""
Role Type Detector — Classifies jobs as skill-heavy, hybrid, or experience-heavy.

This is the gateway to VetLayer's universal scoring system. Before scoring a candidate,
we classify the JD to determine which scoring strategy to apply:

- skill_heavy: Tech roles where hard skill depth is the primary signal
  (e.g., Software Engineer, Data Engineer, DevOps Engineer)

- hybrid: Roles mixing hard skills with leadership/strategic requirements
  (e.g., Engineering Manager, Director of Product, Solutions Architect)

- experience_heavy: Roles where career progression, industry match, and
  soft skill proxies matter more than specific hard skills
  (e.g., HR Manager, Marketing Director, Operations Manager, Sales Executive)
"""

import re
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Soft skill indicators — terms that DON'T produce assessable skill depths
# ═══════════════════════════════════════════════════════════════════════

_SOFT_SKILL_KEYWORDS = {
    "communication", "teamwork", "leadership", "problem solving", "problem-solving",
    "time management", "critical thinking", "interpersonal", "adaptability",
    "work ethic", "attention to detail", "self motivated", "self-motivated",
    "team player", "multitasking", "organizational skills", "negotiation",
    "conflict resolution", "decision making", "decision-making", "creative thinking",
    "emotional intelligence", "collaboration", "presentation skills",
    "stakeholder management", "relationship building", "influencing",
    "strategic thinking", "analytical thinking", "coaching", "mentoring",
    "people management", "change management", "ability to work under pressure",
    "strong communication", "excellent communication", "good communication",
    "verbal and written", "written and verbal",
}

# ═══════════════════════════════════════════════════════════════════════
# Experience-heavy role title patterns
# ═══════════════════════════════════════════════════════════════════════

_EXPERIENCE_HEAVY_TITLE_PATTERNS = [
    r"\b(hr|human resources)\b",
    r"\bmarketing\s*(manager|director|head|vp|lead)\b",
    r"\bsales\s*(manager|director|head|vp|executive|lead)\b",
    r"\boperations\s*(manager|director|head|vp)\b",
    r"\bbusiness\s*development\b",
    r"\baccount\s*(manager|executive|director)\b",
    r"\brelationship\s*manager\b",
    r"\bgeneral\s*manager\b",
    r"\boffice\s*manager\b",
    r"\badmin(istrative)?\s*(manager|director|head|assistant)\b",
    r"\brecruit(ment|er|ing)\s*(manager|director|head|lead)?\b",
    r"\btalent\s*acquisition\b",
    r"\btraining\s*(manager|director|head)\b",
    r"\blearning\s*(&|and)\s*development\b",
    r"\bcustomer\s*(success|experience|service)\s*(manager|director|head|lead)?\b",
    r"\bprocurement\s*(manager|director|head)\b",
    r"\bsupply\s*chain\s*(manager|director|head)?\b",
    r"\blogistics\s*(manager|director|head)?\b",
    r"\bcompliance\s*(manager|director|officer)\b",
    r"\blegal\s*(manager|director|counsel|head)\b",
    r"\bcommunications\s*(manager|director|head)\b",
    r"\bpr\s*(manager|director)\b",
    r"\bpublic\s*relations\b",
    r"\bevent\s*(manager|coordinator|director)\b",
    r"\bfacilities\s*(manager|director)\b",
    # Finance / Accounting
    r"\bfinance\s*(control|controller|manager|director|head|lead|analyst|vp)\b",
    r"\bfinancial\s*(control|controller|analyst|manager|director|planning)\b",
    r"\bcontroller\b",
    r"\btreasur(y|er)\b",
    r"\b(cfo|coo|cro|cmo|chro|cao)\b",
    r"\bchief\s*(financial|operating|risk|marketing|human|people|revenue|strategy|compliance)\b",
    r"\baudit\s*(manager|director|lead|head)\b",
    r"\brisk\s*(manager|director|officer|lead|head)\b",
    r"\bgovernance\b",
    # Consulting / Strategy
    r"\bconsult(ant|ing)\s*(manager|director|partner|lead)?\b",
    r"\bstrategy\s*(manager|director|lead|analyst)\b",
    r"\bchange\s*management\b",
    r"\btransformation\b",
    # Client-facing
    r"\bclient\s*(experience|relationship|success|engagement)\b",
]

_HYBRID_TITLE_PATTERNS = [
    r"\bdirector\b",
    r"\bvp\b",
    r"\bvice\s*president\b",
    r"\bhead\s+of\b",
    r"\bchief\b",
    r"\bengineering\s*manager\b",
    r"\btechnical\s*(program|project)\s*manager\b",
    r"\bsolutions?\s*architect\b",
    r"\bproduct\s*(manager|owner|director|lead)\b",
    r"\bprogram\s*manager\b",
    r"\bproject\s*manager\b",
    r"\bscrum\s*master\b",
    r"\btechnical\s*lead\b",
    r"\btech\s*lead\b",
    r"\bteam\s*lead\b",
]

_SKILL_HEAVY_TITLE_PATTERNS = [
    r"\b(software|backend|frontend|full[\s-]*stack|mobile|web|cloud|data|devops|ml|ai|machine\s*learning)\s*(engineer|developer|programmer)\b",
    r"\bsre\b",
    r"\bsite\s*reliability\b",
    r"\bplatform\s*engineer\b",
    r"\binfrastructure\s*engineer\b",
    r"\bqa\s*(engineer|analyst|lead)\b",
    r"\btest\s*(engineer|automation)\b",
    r"\bsecurity\s*engineer\b",
    r"\bnetwork\s*engineer\b",
    r"\bdatabase\s*(administrator|engineer)\b",
    r"\bdata\s*(scientist|analyst|engineer)\b",
    r"\bdesigner\s*(ui|ux|graphic|web)?\b",
]


# ═══════════════════════════════════════════════════════════════════════
# Non-tech domain skill keywords — skills that exist outside our taxonomy
# ═══════════════════════════════════════════════════════════════════════

_NON_TECH_HARD_SKILLS = {
    # Marketing
    "seo", "sem", "ppc", "google ads", "facebook ads", "social media marketing",
    "content marketing", "email marketing", "marketing automation", "hubspot",
    "mailchimp", "google analytics", "adobe analytics", "media buying",
    "brand management", "market research", "copywriting", "campaign management",
    # Finance / Accounting
    "financial modeling", "financial analysis", "budgeting", "forecasting",
    "gaap", "ifrs", "audit", "tax", "accounts payable", "accounts receivable",
    "reconciliation", "variance analysis", "cost accounting", "treasury",
    "risk management", "portfolio management", "valuation", "m&a",
    # Supply Chain / Operations
    "supply chain management", "inventory management", "procurement",
    "demand planning", "logistics", "warehouse management", "lean manufacturing",
    "six sigma", "quality management", "vendor management", "erp",
    "supply chain optimization", "production planning",
    # Healthcare
    "clinical trials", "clinical trial management", "fda regulations",
    "hipaa", "emr", "ehr", "medical billing", "medical coding",
    "patient care", "pharmacovigilance", "good clinical practice",
    "clinical research", "regulatory affairs",
    # Legal
    "contract management", "contract drafting", "legal research",
    "compliance", "regulatory compliance", "intellectual property",
    "corporate law", "employment law", "litigation", "due diligence",
    # HR
    "talent acquisition", "performance management", "employee relations",
    "compensation and benefits", "hris", "succession planning",
    "organizational development", "workforce planning", "onboarding",
    "employee engagement", "labor law",
    # Sales
    "crm", "salesforce", "pipeline management", "lead generation",
    "cold calling", "territory management", "quota management",
    "contract negotiation", "key account management", "b2b sales", "b2c sales",
    # Real Estate
    "property management", "lease negotiation", "real estate valuation",
    "tenant relations", "zoning", "construction management",
}


def detect_role_type(
    job_title: str,
    job_description: str = "",
    required_skills: list = None,
    preferred_skills: list = None,
) -> Dict[str, Any]:
    """
    Classify a job into one of three categories:
    - skill_heavy: Hard skill depth is the primary evaluation signal
    - hybrid: Mix of skills + experience/leadership
    - experience_heavy: Career progression, industry, and soft skill proxies dominate

    Returns:
    {
        "type": "skill_heavy" | "hybrid" | "experience_heavy",
        "confidence": 0.0-1.0,
        "signals": {
            "soft_skill_ratio": float,
            "hard_skill_count": int,
            "non_tech_skill_count": int,
            "title_signal": str,
            "tech_ratio": float,
            "professional_ratio": float,
            "recognized_ratio": float,
            "domain_profile": dict,
        },
        "scoring_weights": {...}  # Pre-computed weight multipliers for this role type
    }
    """
    if required_skills is None:
        required_skills = []
    if preferred_skills is None:
        preferred_skills = []

    title_lower = (job_title or "").lower().strip()
    desc_lower = (job_description or "").lower()[:3000]

    all_skills = required_skills + preferred_skills
    all_skill_names = [s.get("skill", "").lower() for s in all_skills]

    # ── Signal 1: Title-based classification ───────────────────────────
    title_signal = _classify_by_title(title_lower)

    # ── Signal 2: Soft skill ratio in requirements ─────────────────────
    soft_count = 0
    hard_count = 0
    non_tech_hard_count = 0

    for skill_name in all_skill_names:
        if _is_soft_skill(skill_name):
            soft_count += 1
        else:
            hard_count += 1
            if _is_non_tech_hard_skill(skill_name):
                non_tech_hard_count += 1

    total_skills = soft_count + hard_count
    soft_ratio = soft_count / max(total_skills, 1)

    # ── Signal 3: Description soft skill density ───────────────────────
    desc_soft_count = sum(1 for kw in _SOFT_SKILL_KEYWORDS if kw in desc_lower)
    desc_soft_density = desc_soft_count / max(len(desc_lower.split()), 1) * 100

    # ── Signal 4: Domain profile from skill ontology ───────────────────
    # Instead of checking "is this skill in our tech taxonomy?", we ask
    # "what domain does each skill belong to?" — this decouples role type
    # detection from the evidence extraction aliases entirely.
    from app.services.skill_ontology import compute_domain_profile, resolve_skill

    domain_profile = compute_domain_profile(all_skill_names)
    tech_ratio = domain_profile.get("technology", 0.0)
    unknown_ratio = domain_profile.get("unknown", 0.0)
    # Professional domains that indicate experience-heavy assessment
    _PROFESSIONAL_DOMAINS = {"finance", "hr", "legal", "healthcare",
                             "marketing", "sales", "operations", "consulting"}
    professional_ratio = sum(v for k, v in domain_profile.items()
                             if k in _PROFESSIONAL_DOMAINS)
    # Leadership/general are neutral — they appear in both tech and non-tech roles
    leadership_ratio = domain_profile.get("leadership", 0.0)
    # Count how many skills our ontology recognizes at all
    recognized_count = sum(1 for s in all_skill_names if resolve_skill(s) is not None)
    recognized_ratio = recognized_count / max(len(all_skill_names), 1)

    # ── Combine signals into classification ────────────────────────────
    score_skill_heavy = 0.0
    score_hybrid = 0.0
    score_experience_heavy = 0.0

    # Title signal (strong weight)
    if title_signal == "skill_heavy":
        score_skill_heavy += 0.35
    elif title_signal == "hybrid":
        score_hybrid += 0.35
    elif title_signal == "experience_heavy":
        score_experience_heavy += 0.35

    # Soft skill ratio
    if soft_ratio >= 0.6:
        score_experience_heavy += 0.25
    elif soft_ratio >= 0.3:
        score_hybrid += 0.20
    else:
        score_skill_heavy += 0.20

    # Domain profile signal (replaces coupled assessable_ratio)
    # Technology-dominant JDs → skill_heavy
    # Professional-domain-dominant JDs → experience_heavy
    # Mixed or unrecognized → hybrid or experience_heavy
    if tech_ratio >= 0.5:
        score_skill_heavy += 0.25
    elif tech_ratio >= 0.3 and professional_ratio < 0.3:
        score_hybrid += 0.15
        score_skill_heavy += 0.10
    elif professional_ratio >= 0.3:
        score_experience_heavy += 0.20
    elif unknown_ratio >= 0.5:
        # Most skills aren't in our ontology — likely soft/experiential
        score_experience_heavy += 0.15
    else:
        score_hybrid += 0.15

    # Leadership-heavy JDs lean hybrid/experience
    if leadership_ratio >= 0.3:
        score_hybrid += 0.05
        score_experience_heavy += 0.05

    # Hard skill count
    if hard_count >= 6:
        score_skill_heavy += 0.15
    elif hard_count >= 3:
        score_hybrid += 0.10
    else:
        score_experience_heavy += 0.15

    # Non-tech hard skills
    if non_tech_hard_count >= 3:
        score_hybrid += 0.05  # Has concrete skills but outside our taxonomy

    # Description soft skill density
    if desc_soft_density > 2.0:
        score_experience_heavy += 0.10

    # Determine winner
    scores = {
        "skill_heavy": score_skill_heavy,
        "hybrid": score_hybrid,
        "experience_heavy": score_experience_heavy,
    }
    role_type = max(scores, key=scores.get)
    confidence = scores[role_type] / max(sum(scores.values()), 0.01)

    # Build scoring weight multipliers
    scoring_weights = _get_scoring_weights(role_type)

    result = {
        "type": role_type,
        "confidence": round(confidence, 3),
        "signals": {
            "soft_skill_ratio": round(soft_ratio, 3),
            "hard_skill_count": hard_count,
            "non_tech_skill_count": non_tech_hard_count,
            "title_signal": title_signal,
            "tech_ratio": round(tech_ratio, 3),
            "professional_ratio": round(professional_ratio, 3),
            "recognized_ratio": round(recognized_ratio, 3),
            "domain_profile": {k: round(v, 3) for k, v in domain_profile.items()},
            "desc_soft_density": round(desc_soft_density, 3),
        },
        "scoring_weights": scoring_weights,
    }

    logger.info(
        f"Role type detection: {job_title} -> {role_type} "
        f"(confidence={confidence:.2f}, soft_ratio={soft_ratio:.2f}, "
        f"tech_ratio={tech_ratio:.2f}, prof_ratio={professional_ratio:.2f}, "
        f"recognized={recognized_count}/{len(all_skill_names)}, "
        f"title_signal={title_signal})"
    )

    return result


def _classify_by_title(title_lower: str) -> str:
    """Classify role type based on job title patterns."""
    # Check skill-heavy first (most specific)
    for pattern in _SKILL_HEAVY_TITLE_PATTERNS:
        if re.search(pattern, title_lower):
            return "skill_heavy"

    # Check experience-heavy
    for pattern in _EXPERIENCE_HEAVY_TITLE_PATTERNS:
        if re.search(pattern, title_lower):
            return "experience_heavy"

    # Check hybrid
    for pattern in _HYBRID_TITLE_PATTERNS:
        if re.search(pattern, title_lower):
            return "hybrid"

    return "unknown"


def _is_soft_skill(skill_name: str) -> bool:
    """Check if a skill name is a soft skill that can't be assessed from a resume."""
    name_lower = skill_name.lower().strip()
    # Direct match
    if name_lower in _SOFT_SKILL_KEYWORDS:
        return True
    # Substring match for phrases like "strong communication skills"
    for soft_kw in _SOFT_SKILL_KEYWORDS:
        if soft_kw in name_lower or name_lower in soft_kw:
            return True
    return False


def _is_non_tech_hard_skill(skill_name: str) -> bool:
    """Check if a skill is a hard skill but outside our tech taxonomy."""
    name_lower = skill_name.lower().strip()
    for nts in _NON_TECH_HARD_SKILLS:
        if nts in name_lower or name_lower in nts:
            return True
    return False


def _get_scoring_weights(role_type: str) -> Dict[str, float]:
    """
    Return scoring weight multipliers based on role type.

    For skill-heavy roles: skill depth matters most
    For hybrid: balanced across all dimensions
    For experience-heavy: career trajectory and education matter most
    """
    if role_type == "skill_heavy":
        return {
            "skill_match": 1.20,   # Boost skill importance
            "depth": 1.15,         # Depth matters a lot
            "experience": 0.85,    # Experience matters less
            "education": 0.80,     # Education less critical
            "trajectory": 0.60,    # Career path less important
            "soft_skill_proxy": 0.50,  # Soft skills minimal weight
        }
    elif role_type == "experience_heavy":
        return {
            "skill_match": 0.65,   # Skills matter less
            "depth": 0.60,         # Depth much less important
            "experience": 1.30,    # Experience is primary signal
            "education": 1.15,     # Education matters more
            "trajectory": 1.40,    # Career progression is key
            "soft_skill_proxy": 1.30,  # Soft skill evidence is important
        }
    else:  # hybrid
        return {
            "skill_match": 1.00,
            "depth": 0.90,
            "experience": 1.10,
            "education": 1.00,
            "trajectory": 1.10,
            "soft_skill_proxy": 0.90,
        }
