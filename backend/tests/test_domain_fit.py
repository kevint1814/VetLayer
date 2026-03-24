"""Tests for the domain-fit assessment module."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.domain_fit import assess_domain_fit, _detect_domain, _is_adjacent_domain


# ── Domain detection tests ────────────────────────────────────────────

def test_detects_banking_domain():
    domain, conf, signals = _detect_domain(
        "finance control lec lead banking ifrs 9 wealth management legal entity controller"
    )
    assert domain == "banking"
    assert conf > 0.5
    assert any("banking" in s or "ifrs 9" in s or "wealth" in s for s in signals)


def test_detects_healthcare_domain():
    domain, conf, signals = _detect_domain(
        "clinical operations director hospital patient care hipaa compliance"
    )
    assert domain == "healthcare"
    assert conf > 0.5


def test_detects_technology_domain():
    domain, conf, signals = _detect_domain(
        "senior software engineer microservices cloud platform saas engineering"
    )
    assert domain == "technology"


def test_returns_none_for_generic_jd():
    domain, conf, signals = _detect_domain(
        "general manager operations leadership team management"
    )
    # Generic JD might detect something weakly or return None
    # Either way confidence should be low
    assert domain is None or conf < 0.5


# ── Adjacency tests ───────────────────────────────────────────────────

def test_banking_adjacent_to_insurance():
    assert _is_adjacent_domain("banking", ["insurance"]) is True


def test_banking_not_adjacent_to_healthcare():
    assert _is_adjacent_domain("banking", ["healthcare"]) is False


def test_consulting_adjacent_to_everything():
    assert _is_adjacent_domain("banking", ["consulting"]) is True
    assert _is_adjacent_domain("healthcare", ["consulting"]) is True


# ── Full domain-fit assessment tests ──────────────────────────────────

def test_in_domain_banking_candidate():
    result = assess_domain_fit(
        job_title="Finance Controller - LEC Lead",
        job_description="Banking role requiring IFRS 9, wealth management, legal entity controller",
        parsed_resume={
            "experience": [
                {"title": "Finance Controller", "company": "HSBC Bank",
                 "description": "Led banking operations, IFRS 9 implementation, treasury"},
            ],
            "skills": ["banking", "ifrs 9", "treasury"],
        },
    )
    assert result["jd_domain"] == "banking"
    assert result["domain_match"] == "in_domain"
    assert result["domain_fit_score"] >= 70


def test_adjacent_domain_it_finance_to_banking():
    result = assess_domain_fit(
        job_title="Finance Controller - LEC Lead",
        job_description="Banking role requiring IFRS 9, wealth management, legal entity controller",
        parsed_resume={
            "experience": [
                {"title": "CFO", "company": "Wipro Technologies",
                 "description": "Led finance function for IT services, financial reporting, compliance"},
                {"title": "Head of Finance", "company": "Accenture",
                 "description": "Financial controllership, budgeting, forecasting"},
            ],
            "skills": ["financial reporting", "compliance", "budgeting"],
        },
    )
    assert result["jd_domain"] == "banking"
    assert result["domain_match"] in ("adjacent", "out_of_domain")
    assert result["domain_fit_score"] < 80


def test_domain_agnostic_role():
    result = assess_domain_fit(
        job_title="Project Manager",
        job_description="Lead cross-functional projects, manage timelines and stakeholders",
        parsed_resume={
            "experience": [
                {"title": "Project Manager", "company": "ABC Corp",
                 "description": "Managed enterprise projects"},
            ],
        },
    )
    # Generic JD should be domain-agnostic or very low confidence
    assert result["domain_match"] in ("domain_agnostic", "in_domain", "adjacent")


def test_domain_gaps_detected():
    result = assess_domain_fit(
        job_title="Finance Controller - LEC Lead",
        job_description="Banking role requiring IFRS 9, product control, legal entity controller, wealth management, loans",
        parsed_resume={
            "experience": [
                {"title": "Finance Manager", "company": "Tech Corp",
                 "description": "General financial management and reporting"},
            ],
            "skills": ["financial reporting"],
        },
    )
    # Should detect domain gaps for banking-specific skills
    if result["jd_domain"] == "banking":
        assert len(result["domain_gaps"]) > 0


def test_risk_summary_present_for_non_domain_match():
    result = assess_domain_fit(
        job_title="Finance Controller",
        job_description="Banking retail lending IFRS 9 wealth management",
        parsed_resume={
            "experience": [
                {"title": "Finance Lead", "company": "Retail Corp",
                 "description": "Retail finance operations, merchandising"},
            ],
            "skills": ["retail", "merchandising"],
        },
    )
    if result["domain_match"] != "domain_agnostic":
        assert result["domain_risk_summary"] != ""
