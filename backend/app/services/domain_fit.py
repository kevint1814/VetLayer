"""
Domain-Fit Assessment Module for VetLayer Universal Scoring.

Extracts the industry/domain context from the JD and evaluates whether
the candidate's experience is in-domain, adjacent, or out-of-domain.

This is fundamentally different from role_type detection (which classifies
skill_heavy vs experience_heavy). Domain-fit answers: "Is this candidate's
experience in the same industry/domain as the JD?"

Example:
  - JD: "Finance Controller at a global bank, IFRS 9, wealth/retail loans"
    → Domain: banking/financial_services
  - Candidate: CFO at IT services company
    → Domain match: adjacent (finance skills transfer, but banking domain gap)
    → Flag: "Banking-domain fit unproven — validate IFRS 9, product control,
             and legal entity controller experience in a banking context"
"""

import re
from typing import Optional


# ── Domain keyword taxonomy ────────────────────────────────────────────
# Maps industry domains to sets of keywords found in JDs.
# Ordered by specificity: more specific domains listed first.
_DOMAIN_KEYWORDS = {
    "banking": [
        "bank", "banking", "retail banking", "corporate banking",
        "investment banking", "commercial banking", "wealth management",
        "private banking", "loans", "lending", "credit", "mortgage",
        "ifrs 9", "basel", "capital adequacy", "liquidity coverage",
        "legal entity controller", "product control", "treasury operations",
        "trade finance", "payments", "core banking", "right-shoring",
        "country cfo", "segment cfo",
    ],
    "insurance": [
        "insurance", "underwriting", "claims", "actuarial",
        "reinsurance", "policy administration", "ifrs 17",
        "solvency ii", "loss ratio", "premium",
    ],
    "asset_management": [
        "asset management", "fund management", "portfolio management",
        "hedge fund", "private equity", "venture capital",
        "aum", "assets under management", "fund accounting",
    ],
    "healthcare": [
        "healthcare", "hospital", "clinical", "pharmaceutical",
        "biotech", "medical device", "patient", "hipaa",
        "electronic health record", "ehr", "fda", "drug",
        "clinical trial", "life sciences",
    ],
    "technology": [
        "saas", "software", "platform", "cloud", "api",
        "engineering", "devops", "machine learning", "ai",
        "tech stack", "microservices", "infrastructure",
    ],
    "retail": [
        "retail", "e-commerce", "ecommerce", "omnichannel",
        "merchandising", "store operations", "point of sale",
        "consumer goods", "fmcg", "cpg",
    ],
    "manufacturing": [
        "manufacturing", "production", "assembly", "plant",
        "lean manufacturing", "supply chain", "warehouse",
        "quality control", "iso 9001", "factory",
    ],
    "consulting": [
        "consulting", "advisory", "professional services",
        "management consulting", "strategy consulting",
        "big four", "engagement manager",
    ],
    "telecommunications": [
        "telecom", "telecommunications", "network operator",
        "mobile network", "5g", "fiber", "spectrum",
    ],
    "energy": [
        "energy", "oil and gas", "renewable", "solar", "wind",
        "utilities", "power generation", "upstream", "downstream",
    ],
    "government": [
        "government", "public sector", "federal", "state agency",
        "municipal", "defense", "military", "civil service",
    ],
    "education": [
        "university", "higher education", "edtech", "education",
        "academic", "student", "curriculum", "learning management",
    ],
    "real_estate": [
        "real estate", "property", "reit", "construction",
        "facility management", "leasing", "commercial property",
    ],
}

# ── Domain-specific critical skills ────────────────────────────────────
# When a JD is in a specific domain, these skills become especially important.
# If the candidate lacks evidence of these, it's a domain-fit risk.
_DOMAIN_CRITICAL_SKILLS = {
    "banking": [
        "ifrs 9", "product control", "legal entity control",
        "regulatory reporting", "capital adequacy", "basel",
        "banking operations", "wealth management", "lending",
        "credit risk", "treasury", "core banking",
    ],
    "insurance": [
        "actuarial", "underwriting", "claims processing",
        "ifrs 17", "solvency", "policy administration",
    ],
    "healthcare": [
        "hipaa", "clinical operations", "patient care",
        "regulatory affairs", "fda compliance", "ehr",
    ],
    "asset_management": [
        "portfolio management", "fund accounting",
        "nav calculation", "regulatory reporting",
    ],
}

# ── Industry keywords in candidate experience ─────────────────────────
_CANDIDATE_INDUSTRY_SIGNALS = {
    "banking": [
        "bank", "banking", "financial services", "wealth", "lending",
        "loans", "credit", "mortgage", "treasury", "payments",
        "ifrs 9", "basel", "product control",
    ],
    "insurance": [
        "insurance", "underwriting", "claims", "actuarial",
        "reinsurance", "premium", "policy",
    ],
    "healthcare": [
        "healthcare", "hospital", "clinical", "pharmaceutical",
        "biotech", "medical", "patient", "hipaa",
    ],
    "technology": [
        "software", "saas", "technology", "tech", "platform",
        "engineering", "startup", "cloud", "cybersecurity",
    ],
    "consulting": [
        "consulting", "advisory", "professional services",
        "accenture", "deloitte", "pwc", "kpmg", "ey",
        "mckinsey", "bcg", "bain", "wipro", "infosys", "tcs",
        "cognizant", "capgemini",
    ],
    "retail": [
        "retail", "e-commerce", "consumer", "fmcg", "store",
        "merchandising", "shoppers",
    ],
    "manufacturing": [
        "manufacturing", "production", "plant", "factory",
        "assembly", "quality control",
    ],
}


def assess_domain_fit(
    job_title: str,
    job_description: str,
    parsed_resume: dict,
    required_skills: list = None,
) -> dict:
    """
    Assess how well the candidate's industry/domain experience matches
    the JD's domain context.

    Returns:
        {
            "jd_domain": str or None,         # Primary domain detected in JD
            "jd_domain_confidence": float,     # 0-1 confidence in domain detection
            "jd_domain_signals": list,         # Keywords found in JD
            "candidate_domains": list,         # Domains the candidate has worked in
            "domain_match": str,               # "in_domain", "adjacent", "out_of_domain"
            "domain_fit_score": float,         # 0-100
            "domain_gaps": list,               # Specific domain skills missing
            "domain_risk_summary": str,        # One-line risk summary for recruiters
        }
    """
    if required_skills is None:
        required_skills = []

    jd_text = f"{job_title} {job_description}".lower()

    # Also include required skill names in JD text for domain detection
    for s in required_skills:
        skill_name = s.get("skill", "")
        if skill_name:
            jd_text += f" {skill_name.lower()}"

    # ── Step 1: Detect JD domain ──────────────────────────────────────
    jd_domain, jd_confidence, jd_signals = _detect_domain(jd_text)

    # ── Step 2: Detect candidate domains ──────────────────────────────
    candidate_text = _build_candidate_text(parsed_resume)
    candidate_domains = _detect_candidate_domains(candidate_text)

    # ── Step 3: Compute domain match ──────────────────────────────────
    if not jd_domain:
        # JD doesn't have a strong domain signal — domain-agnostic role
        return {
            "jd_domain": None,
            "jd_domain_confidence": 0.0,
            "jd_domain_signals": [],
            "candidate_domains": candidate_domains,
            "domain_match": "domain_agnostic",
            "domain_fit_score": 80,  # Neutral — no domain requirement
            "domain_gaps": [],
            "domain_risk_summary": "",
        }

    # Check if candidate has in-domain experience
    candidate_domain_names = [d["domain"] for d in candidate_domains]

    if jd_domain in candidate_domain_names:
        match_type = "in_domain"
        base_score = 90
    elif _is_adjacent_domain(jd_domain, candidate_domain_names):
        match_type = "adjacent"
        base_score = 60
    else:
        match_type = "out_of_domain"
        base_score = 30

    # ── Step 4: Check domain-critical skills ──────────────────────────
    domain_gaps = []
    critical_skills = _DOMAIN_CRITICAL_SKILLS.get(jd_domain, [])
    if critical_skills:
        for cs in critical_skills:
            # Check if this critical skill appears in the JD
            if cs.lower() in jd_text:
                # Check if candidate has evidence of it
                found = cs.lower() in candidate_text
                if not found:
                    domain_gaps.append(cs)

    # Adjust score based on domain gaps
    if domain_gaps:
        gap_penalty = min(len(domain_gaps) * 8, 30)
        base_score = max(base_score - gap_penalty, 10)

    # ── Step 5: Build risk summary ────────────────────────────────────
    risk_summary = _build_risk_summary(
        jd_domain, match_type, candidate_domains, domain_gaps, job_title
    )

    return {
        "jd_domain": jd_domain,
        "jd_domain_confidence": round(jd_confidence, 2),
        "jd_domain_signals": jd_signals[:10],
        "candidate_domains": candidate_domains,
        "domain_match": match_type,
        "domain_fit_score": base_score,
        "domain_gaps": domain_gaps,
        "domain_risk_summary": risk_summary,
    }


def _detect_domain(text: str) -> tuple:
    """Detect primary domain from text. Returns (domain, confidence, signals)."""
    best_domain = None
    best_score = 0
    best_signals = []

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        signals = []
        score = 0
        for kw in keywords:
            # Use word boundary matching for short keywords
            if len(kw) <= 4:
                pattern = r'\b' + re.escape(kw) + r'\b'
            else:
                pattern = re.escape(kw)
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                signals.append(kw)
                # Longer/more specific keywords score higher
                score += len(matches) * (1 + len(kw) / 10)

        if score > best_score:
            best_score = score
            best_domain = domain
            best_signals = signals

    # Confidence is based on how many unique keywords matched
    # and how far ahead the top domain is
    if best_score == 0:
        return None, 0.0, []

    confidence = min(len(best_signals) / 4, 1.0)  # 4+ unique signals = full confidence
    return best_domain, confidence, best_signals


def _build_candidate_text(parsed_resume: dict) -> str:
    """Build searchable text from candidate's full resume."""
    parts = []

    # Experience
    for exp in parsed_resume.get("experience") or []:
        parts.append(exp.get("title", ""))
        parts.append(exp.get("company", ""))
        parts.append(exp.get("description", ""))
        for tech in exp.get("technologies") or []:
            parts.append(tech)

    # Skills
    for s in parsed_resume.get("skills") or []:
        if isinstance(s, str):
            parts.append(s)
        elif isinstance(s, dict):
            parts.append(s.get("name", ""))

    # Summary
    parts.append(parsed_resume.get("summary", ""))

    # Certifications
    for cert in parsed_resume.get("certifications") or []:
        if isinstance(cert, str):
            parts.append(cert)
        elif isinstance(cert, dict):
            parts.append(cert.get("name", ""))

    return " ".join(p for p in parts if p).lower()


def _detect_candidate_domains(candidate_text: str) -> list:
    """Detect all domains the candidate has experience in."""
    domains = []

    for domain, keywords in _CANDIDATE_INDUSTRY_SIGNALS.items():
        signals = []
        for kw in keywords:
            if len(kw) <= 4:
                pattern = r'\b' + re.escape(kw) + r'\b'
            else:
                pattern = re.escape(kw)
            if re.search(pattern, candidate_text, re.IGNORECASE):
                signals.append(kw)

        if len(signals) >= 2:  # Need at least 2 signals to count
            domains.append({
                "domain": domain,
                "signal_count": len(signals),
                "signals": signals[:5],
            })

    # Sort by signal count (strongest domain first)
    domains.sort(key=lambda d: d["signal_count"], reverse=True)
    return domains


# ── Adjacency map: which domains transfer to which ────────────────────
_ADJACENT_DOMAINS = {
    "banking": {"insurance", "asset_management", "consulting"},
    "insurance": {"banking", "asset_management", "consulting"},
    "asset_management": {"banking", "insurance", "consulting"},
    "healthcare": {"consulting"},
    "technology": {"consulting"},
    "retail": {"manufacturing", "consulting"},
    "manufacturing": {"retail", "consulting"},
    "consulting": {
        "banking", "insurance", "healthcare", "technology",
        "retail", "manufacturing", "asset_management",
    },
}


def _is_adjacent_domain(jd_domain: str, candidate_domains: list) -> bool:
    """Check if any candidate domain is adjacent to the JD domain."""
    adjacent = _ADJACENT_DOMAINS.get(jd_domain, set())
    return bool(adjacent & set(candidate_domains))


def _build_risk_summary(
    jd_domain: str,
    match_type: str,
    candidate_domains: list,
    domain_gaps: list,
    job_title: str,
) -> str:
    """Build a one-line domain-fit risk summary for recruiters."""
    domain_label = jd_domain.replace("_", " ").title()
    cand_domain_labels = [d["domain"].replace("_", " ").title()
                          for d in candidate_domains[:2]]

    if match_type == "in_domain":
        if domain_gaps:
            return (
                f"In-domain {domain_label} experience confirmed, but specific "
                f"gaps in {', '.join(domain_gaps[:3])} should be validated in interview."
            )
        return f"Strong {domain_label} domain fit — candidate has direct industry experience."

    elif match_type == "adjacent":
        cand_exp = f" ({', '.join(cand_domain_labels)} background)" if cand_domain_labels else ""
        gap_note = ""
        if domain_gaps:
            gap_note = f" Key domain-specific gaps: {', '.join(domain_gaps[:3])}."
        return (
            f"Adjacent-domain candidate{cand_exp} — transferable skills exist but "
            f"{domain_label}-specific fit must be validated.{gap_note}"
        )

    else:  # out_of_domain
        cand_exp = f" (experience in {', '.join(cand_domain_labels)})" if cand_domain_labels else ""
        return (
            f"Out-of-domain candidate{cand_exp} for a {domain_label} role. "
            f"Core competencies may transfer but industry-specific knowledge is unproven."
        )
