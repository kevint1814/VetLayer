"""
Soft Skill Proxy Detector — Extracts behavioral evidence of soft skills from resumes.

VetLayer by design rates soft skills at depth 0 (they can't be reliably assessed
from resumes). But for experience-heavy and hybrid roles, we need SOME signal
about leadership, communication, problem-solving, etc.

Instead of rating soft skills directly, this module detects PROXY EVIDENCE:
behavioral indicators that imply soft skill competency.

Examples:
  - "Managed a team of 15 engineers" → Leadership proxy
  - "Presented to C-suite executives" → Communication proxy
  - "Reduced customer churn by 30%" → Problem-solving proxy
  - "Led cross-functional initiative across 4 departments" → Collaboration proxy
"""

import re
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Soft skill proxy patterns
# Each pattern: (regex, evidence_strength, description_template)
# ═══════════════════════════════════════════════════════════════════════

_LEADERSHIP_PROXIES = [
    (r"managed?\s+(?:a\s+)?team\s+of\s+(\d+)", 0.90, "Managed team of {0} people"),
    (r"led\s+(?:a\s+)?team\s+of\s+(\d+)", 0.90, "Led team of {0} people"),
    (r"supervised\s+(\d+)\s+(?:direct\s+)?report", 0.85, "Supervised {0} direct reports"),
    (r"(?:mentored?|coached?)\s+(\d+)\s+(?:junior|engineer|developer|staff|team member|analyst)", 0.75, "Mentored {0} team members"),
    (r"(?:directed?|oversaw|headed)\s+(?:a\s+)?(?:team|group|department|division)", 0.80, "Directed team/department"),
    (r"(?:built|grew|scaled)\s+(?:the\s+)?team\s+from\s+(\d+)\s+to\s+(\d+)", 0.90, "Grew team from {0} to {1}"),
    (r"(?:reporting|reported)\s+(?:directly?\s+)?to\s+(?:the\s+)?(?:ceo|cto|cfo|coo|vp|director|president|board)", 0.75, "Reported to senior leadership"),
    (r"(?:people|team|staff|engineering)\s+manager", 0.85, "People management role"),
    (r"promoted?\s+to\s+(?:senior|lead|manager|director|head|vp)", 0.80, "Promoted to senior role"),
    (r"(?:cross[- ]functional|cross[- ]team|cross[- ]departmental)\s+(?:lead|leadership|initiative|collaboration|project)", 0.80, "Led cross-functional initiatives"),
    (r"org[- ]?wide\s+(?:initiative|project|transformation|change)", 0.75, "Led org-wide initiative"),
    (r"(?:founded?|co-founded?|established)\s+(?:the\s+)?(?:company|startup|department|team|practice)", 0.90, "Founded or established organization/department"),
]

_COMMUNICATION_PROXIES = [
    (r"presented?\s+(?:to|at|for)\s+(?:c-suite|c suite|board|executive|leadership|stakeholder|conference|summit)", 0.85, "Presented to senior stakeholders"),
    (r"(?:public\s+speak|keynote|conference\s+(?:speaker|talk|presentation))", 0.85, "Public speaking or conference presentations"),
    (r"(?:authored?|wrote|published)\s+(?:technical\s+)?(?:documentation|blog|article|whitepaper|paper|report)", 0.75, "Authored technical documentation or publications"),
    (r"(?:conducted?|delivered?)\s+(?:training|workshop|seminar|presentation)", 0.75, "Delivered training or workshops"),
    (r"(?:stakeholder|client|customer)\s+(?:communication|presentation|engagement|relationship)", 0.70, "Stakeholder communication and engagement"),
    (r"(?:wrote|drafted|created)\s+(?:proposal|rfp|rfi|business\s+case|executive\s+summary)", 0.75, "Created business proposals or executive communications"),
    (r"(?:liaison|bridge|interface)\s+between\s+(?:technical|business|engineering|product|client)", 0.70, "Bridged technical and business communication"),
    (r"(?:client[- ]facing|customer[- ]facing)\s+(?:role|position|experience)", 0.70, "Client-facing experience"),
]

_PROBLEM_SOLVING_PROXIES = [
    (r"(?:reduced?|decreased?|cut|lowered?)\s+(?:\w+\s+){0,3}by\s+(\d+)\s*%", 0.85, "Achieved {0}% reduction in key metric"),
    (r"(?:increased?|improved?|boosted?|grew)\s+(?:\w+\s+){0,3}by\s+(\d+)\s*%", 0.85, "Achieved {0}% improvement in key metric"),
    (r"(?:optimized?|streamlined?|automated?)\s+(?:\w+\s+){0,3}(?:reducing|saving|cutting)\s+(\d+)", 0.80, "Optimized processes with measurable impact"),
    (r"(?:saved?|delivered?|generated?)\s+\$?\s*(\d[\d,.]*)\s*(?:million|m|k|thousand)", 0.90, "Delivered ${0} in business value"),
    (r"(?:scaled?\s+(?:to|from)\s+(\d[\d,.]*)\s*(?:users?|customer|client|request|transaction))", 0.80, "Scaled system to {0} users/transactions"),
    (r"(?:designed?|architected?|built)\s+(?:end-to-end|from\s+scratch|greenfield)", 0.80, "Designed and built systems from scratch"),
    (r"(?:troubleshot|debugged?|resolved?|fixed)\s+(?:critical|production|high-priority|p0|p1)", 0.75, "Resolved critical production issues"),
    (r"(?:patent|invention|novel\s+approach|innovative\s+solution|first[- ]of[- ]its[- ]kind)", 0.85, "Innovative problem solving or patents"),
]

_COLLABORATION_PROXIES = [
    (r"(?:cross[- ]functional|cross[- ]team|cross[- ]departmental)\s+(?:team|collaboration|project|initiative|partnership)", 0.80, "Cross-functional collaboration"),
    (r"(?:partnered?|collaborated?|worked\s+closely)\s+with\s+(?:product|design|marketing|sales|business|engineering|ops|legal)", 0.75, "Partnered across departments"),
    (r"(?:mentored?|coached?|trained?|onboarded?)\s+(?:new\s+)?(?:hire|employee|team\s+member|engineer|developer|staff)", 0.75, "Mentored and trained team members"),
    (r"(?:code\s+review|peer\s+review|design\s+review)\s*(?:for|across|with)?", 0.60, "Active in peer review processes"),
    (r"(?:open\s+source|oss)\s+(?:contributor|contribution|maintainer|committer)", 0.75, "Open source contributor"),
    (r"(?:agile|scrum|kanban)\s+(?:team|environment|process|methodology)", 0.55, "Worked in agile team environment"),
    (r"(?:remote|distributed|global)\s+team", 0.60, "Experience with remote/distributed teams"),
]

_STRATEGIC_THINKING_PROXIES = [
    (r"(?:developed?|defined?|created?)\s+(?:the\s+)?(?:\w+\s+)?(?:strategy|roadmap|vision|plan|framework)", 0.80, "Developed strategic roadmaps or frameworks"),
    (r"(?:business\s+impact|roi|revenue\s+impact|cost\s+saving|p&l|profit\s+and\s+loss)", 0.75, "Business impact and ROI awareness"),
    (r"(?:market\s+research|competitive\s+analysis|industry\s+analysis|market\s+analysis)", 0.75, "Market and competitive analysis"),
    (r"(?:budget|p&l|cost\s+center|revenue)\s+(?:management|responsibility|ownership|of\s+\$)", 0.80, "Budget or P&L management"),
    (r"(?:kpi|okr|metric|dashboard)\s+(?:definition|tracking|ownership|development)", 0.70, "KPI/OKR definition and tracking"),
    (r"(?:digital\s+transformation|process\s+improvement|organizational\s+change|change\s+management)", 0.75, "Led transformation or change initiatives"),
]


# Map proxy categories to the pattern lists
_ALL_PROXY_CATEGORIES = {
    "leadership": _LEADERSHIP_PROXIES,
    "communication": _COMMUNICATION_PROXIES,
    "problem_solving": _PROBLEM_SOLVING_PROXIES,
    "collaboration": _COLLABORATION_PROXIES,
    "strategic_thinking": _STRATEGIC_THINKING_PROXIES,
}


def detect_soft_skill_proxies(parsed_resume: dict) -> Dict[str, Any]:
    """
    Scan a parsed resume for behavioral evidence of soft skills.

    Returns:
    {
        "soft_skills": [
            {
                "category": "leadership",
                "evidence": "Managed team of 15 engineers at Wipro",
                "strength": 0.90,
                "source": "experience"
            },
            ...
        ],
        "summary": {
            "leadership": {"count": 3, "max_strength": 0.90},
            "communication": {"count": 1, "max_strength": 0.75},
            ...
        },
        "soft_skill_score": 0-100,
        "strongest_areas": ["leadership", "problem_solving"],
        "weakest_areas": ["communication"],
    }
    """
    all_evidence = []

    # Scan experience descriptions
    experiences = parsed_resume.get("experience") or []
    for exp in experiences:
        company = exp.get("company") or ""
        title = exp.get("title") or exp.get("role") or ""
        description = exp.get("description") or ""

        # Also check the title itself for leadership signals
        text_to_scan = f"{title}. {description}"

        for category, patterns in _ALL_PROXY_CATEGORIES.items():
            for pattern, strength, template in patterns:
                matches = re.finditer(pattern, text_to_scan, re.IGNORECASE)
                for match in matches:
                    # Build evidence description
                    groups = match.groups()
                    try:
                        evidence_desc = template.format(*groups) if groups else template
                    except (IndexError, KeyError):
                        evidence_desc = template

                    # Add company context
                    if company:
                        evidence_desc = f"{evidence_desc} at {company}"

                    all_evidence.append({
                        "category": category,
                        "evidence": evidence_desc,
                        "strength": strength,
                        "source": "experience",
                        "matched_text": match.group(0)[:100],
                    })

    # Scan summary / profile section
    summary_text = parsed_resume.get("summary") or parsed_resume.get("profile") or ""
    if summary_text:
        for category, patterns in _ALL_PROXY_CATEGORIES.items():
            for pattern, strength, template in patterns:
                matches = re.finditer(pattern, summary_text, re.IGNORECASE)
                for match in matches:
                    groups = match.groups()
                    try:
                        evidence_desc = template.format(*groups) if groups else template
                    except (IndexError, KeyError):
                        evidence_desc = template
                    all_evidence.append({
                        "category": category,
                        "evidence": evidence_desc,
                        "strength": strength * 0.8,  # Lower weight from summary (more self-reported)
                        "source": "summary",
                        "matched_text": match.group(0)[:100],
                    })

    # Scan certifications / education for leadership signals
    certifications = parsed_resume.get("certifications") or []
    for cert in certifications:
        cert_name = (cert.get("name", "") if isinstance(cert, dict) else str(cert)).lower()
        # PMP, MBA, executive programs signal leadership/strategic thinking
        if any(kw in cert_name for kw in ["pmp", "prince2", "six sigma", "lean"]):
            all_evidence.append({
                "category": "strategic_thinking",
                "evidence": f"Holds {cert_name.upper()} certification",
                "strength": 0.70,
                "source": "certification",
                "matched_text": cert_name,
            })
        if "mba" in cert_name or "executive" in cert_name:
            all_evidence.append({
                "category": "leadership",
                "evidence": f"Holds {cert_name.upper()} degree/certification",
                "strength": 0.70,
                "source": "education",
                "matched_text": cert_name,
            })

    education = parsed_resume.get("education") or []
    for edu in education:
        degree = (edu.get("degree") or "").lower()
        if "mba" in degree or "master of business" in degree:
            all_evidence.append({
                "category": "leadership",
                "evidence": "Holds MBA degree",
                "strength": 0.70,
                "source": "education",
                "matched_text": degree,
            })
            all_evidence.append({
                "category": "strategic_thinking",
                "evidence": "MBA training in business strategy",
                "strength": 0.65,
                "source": "education",
                "matched_text": degree,
            })

    # Deduplicate (same category + similar evidence)
    all_evidence = _deduplicate_evidence(all_evidence)

    # Build summary per category
    summary = {}
    for category in _ALL_PROXY_CATEGORIES:
        cat_evidence = [e for e in all_evidence if e["category"] == category]
        summary[category] = {
            "count": len(cat_evidence),
            "max_strength": max((e["strength"] for e in cat_evidence), default=0.0),
            "evidence": [e["evidence"] for e in cat_evidence[:3]],  # Top 3
        }

    # Compute overall soft skill score
    soft_skill_score = _compute_soft_skill_score(summary)

    # Identify strongest and weakest areas
    sorted_categories = sorted(summary.items(), key=lambda x: x[1]["count"] * x[1]["max_strength"], reverse=True)
    strongest = [cat for cat, data in sorted_categories if data["count"] >= 1 and data["max_strength"] >= 0.7][:3]
    weakest = [cat for cat, data in sorted_categories if data["count"] == 0]

    result = {
        "soft_skills": all_evidence[:20],  # Cap at 20 evidence items
        "summary": summary,
        "soft_skill_score": round(soft_skill_score),
        "strongest_areas": strongest,
        "weakest_areas": weakest,
    }

    logger.info(
        f"Soft skill proxy detection: found {len(all_evidence)} evidence items, "
        f"score={soft_skill_score:.0f}, strongest={strongest}, weakest={weakest}"
    )

    return result


def _deduplicate_evidence(evidence_list: list) -> list:
    """Remove duplicate evidence items (same category + very similar text)."""
    seen = set()
    deduped = []

    for ev in evidence_list:
        # Use category + first 50 chars of matched text as dedup key
        key = f"{ev['category']}|{ev.get('matched_text', '')[:50].lower()}"
        if key not in seen:
            seen.add(key)
            deduped.append(ev)

    return deduped


def _compute_soft_skill_score(summary: dict) -> float:
    """
    Compute a 0-100 soft skill score from the evidence summary.

    Scoring: each category contributes up to 20 points (5 categories = 100 max)
    Points based on evidence count and strength.
    """
    score = 0.0

    for category, data in summary.items():
        count = data["count"]
        max_strength = data["max_strength"]

        if count == 0:
            category_score = 0
        elif count == 1:
            category_score = max_strength * 10
        elif count == 2:
            category_score = max_strength * 14
        elif count >= 3:
            category_score = max_strength * 18
        else:
            category_score = 0

        score += min(category_score, 20)

    return max(0, min(100, score))


def get_soft_skill_gaps_for_role(
    soft_skill_result: dict,
    job_title: str = "",
    role_type: str = "hybrid",
) -> List[Dict[str, str]]:
    """
    Identify soft skill gaps based on what the role typically requires.

    Returns list of gap descriptions for risk flag generation.
    """
    gaps = []
    title_lower = job_title.lower()
    summary = soft_skill_result.get("summary", {})

    # Leadership expectation based on title
    leadership_titles = ["manager", "director", "head", "lead", "vp", "vice president", "chief", "senior"]
    expects_leadership = any(kw in title_lower for kw in leadership_titles)

    if expects_leadership and summary.get("leadership", {}).get("count", 0) == 0:
        gaps.append({
            "category": "leadership",
            "title": "No leadership evidence detected",
            "description": (
                f"The role '{job_title}' typically requires leadership experience, "
                f"but no evidence of team management, mentoring, or people leadership "
                f"was found in the resume. Probe leadership experience in the interview."
            ),
            "severity": "medium",
        })

    # Communication expectation
    comm_titles = ["director", "vp", "head", "client", "customer", "account", "sales", "marketing", "communications"]
    expects_communication = any(kw in title_lower for kw in comm_titles)

    if expects_communication and summary.get("communication", {}).get("count", 0) == 0:
        gaps.append({
            "category": "communication",
            "title": "No communication evidence detected",
            "description": (
                f"The role '{job_title}' typically requires strong communication skills, "
                f"but no evidence of presentations, stakeholder communication, or technical writing "
                f"was found in the resume."
            ),
            "severity": "low",
        })

    # Strategic thinking for senior roles
    strategic_titles = ["director", "vp", "vice president", "chief", "head of", "general manager"]
    expects_strategic = any(kw in title_lower for kw in strategic_titles)

    if expects_strategic and summary.get("strategic_thinking", {}).get("count", 0) == 0:
        gaps.append({
            "category": "strategic_thinking",
            "title": "No strategic thinking evidence detected",
            "description": (
                f"The role '{job_title}' typically requires strategic thinking, "
                f"but no evidence of roadmap development, business impact metrics, "
                f"or strategic planning was found in the resume."
            ),
            "severity": "low",
        })

    return gaps
