"""
Tests for resume parser post-processing fixes:
1. years_experience recalculation from actual dates
2. education_level inference from professional certifications
3. Experience entry validation
"""

import pytest
from unittest.mock import patch
from app.services.resume_parser import _postprocess_extraction, _PROFESSIONAL_CERT_KEYWORDS


class TestYearsExperienceRecalculation:
    """Test that years_experience is recalculated from actual experience dates."""

    def test_corrects_stale_self_reported_years(self):
        """LLM echoes '19 years' from resume text but actual start is 2004 → ~22 years."""
        structured = {
            "years_experience": 19,
            "experience": [
                {"company": "Aujas", "title": "CFO", "start_date": "Nov 2022", "end_date": "present"},
                {"company": "Wipro", "title": "Head of Finance", "start_date": "Apr 2010", "end_date": "Sep 2022"},
                {"company": "Unichem", "title": "Asst Mgr", "start_date": "Nov 2004", "end_date": "Dec 2005"},
            ],
        }
        result = _postprocess_extraction(structured)
        # Should be ~21-22 years (2026 - 2004), not 19
        assert result["years_experience"] >= 21

    def test_keeps_accurate_llm_value(self):
        """If LLM value is within 2 years of calculated, keep it."""
        structured = {
            "years_experience": 10,
            "experience": [
                {"company": "A", "title": "Dev", "start_date": "Jan 2015", "end_date": "present"},
            ],
        }
        result = _postprocess_extraction(structured)
        assert result["years_experience"] == 10  # Within tolerance

    def test_fills_missing_years(self):
        """If LLM returns None, calculate from dates."""
        structured = {
            "years_experience": None,
            "experience": [
                {"company": "A", "title": "Dev", "start_date": "2018", "end_date": "present"},
            ],
        }
        result = _postprocess_extraction(structured)
        assert result["years_experience"] is not None
        assert result["years_experience"] >= 7

    def test_handles_no_experience(self):
        """No experience entries → don't crash."""
        structured = {"years_experience": None, "experience": []}
        result = _postprocess_extraction(structured)
        assert result["years_experience"] is None

    def test_handles_missing_dates(self):
        """Experiences with no parseable dates → keep LLM value."""
        structured = {
            "years_experience": 5,
            "experience": [
                {"company": "A", "title": "Dev", "start_date": "Recently", "end_date": "Current"},
            ],
        }
        result = _postprocess_extraction(structured)
        assert result["years_experience"] == 5  # No correction possible


class TestEducationLevelInference:
    """Test that education_level is inferred from professional certifications."""

    def test_infers_professional_from_aca(self):
        structured = {
            "education_level": None,
            "certifications": [{"name": "ACA", "issuer": "ICAI", "date": None}],
            "skills_mentioned": [],
            "summary": "",
        }
        result = _postprocess_extraction(structured)
        assert result["education_level"] == "Professional"

    def test_infers_professional_from_cfa(self):
        structured = {
            "education_level": None,
            "certifications": [{"name": "CFA Level 2", "issuer": "CFA Institute", "date": None}],
            "skills_mentioned": [],
            "summary": "",
        }
        result = _postprocess_extraction(structured)
        assert result["education_level"] == "Professional"

    def test_infers_from_summary_text(self):
        """Professional cert mentioned in summary but not in certifications list."""
        structured = {
            "education_level": None,
            "certifications": [],
            "skills_mentioned": [],
            "summary": "Experienced ACA professional with 15 years in finance",
        }
        result = _postprocess_extraction(structured)
        assert result["education_level"] == "Professional"

    def test_does_not_override_existing_degree(self):
        """If education_level already has a value, don't override."""
        structured = {
            "education_level": "Master's",
            "certifications": [{"name": "CPA", "issuer": "AICPA", "date": None}],
            "skills_mentioned": [],
            "summary": "",
        }
        result = _postprocess_extraction(structured)
        assert result["education_level"] == "Master's"  # Not changed

    def test_no_false_positive_on_aca_in_academic(self):
        """'aca' in 'academic' should NOT trigger — we use word boundaries."""
        structured = {
            "education_level": None,
            "certifications": [],
            "skills_mentioned": [],
            "summary": "Strong academic background in engineering",
        }
        result = _postprocess_extraction(structured)
        assert result["education_level"] is None  # Not inferred

    def test_infers_from_skills_mentioned(self):
        structured = {
            "education_level": None,
            "certifications": [],
            "skills_mentioned": ["Financial Analysis", "CPA", "GAAP"],
            "summary": "",
        }
        result = _postprocess_extraction(structured)
        assert result["education_level"] == "Professional"

    def test_handles_none_certifications(self):
        structured = {
            "education_level": None,
            "certifications": None,
            "skills_mentioned": None,
            "summary": None,
        }
        result = _postprocess_extraction(structured)
        assert result["education_level"] is None  # No crash

    def test_handles_string_cert_entries(self):
        """Some LLMs return certifications as strings instead of dicts."""
        structured = {
            "education_level": None,
            "certifications": ["ACA", "ACS", "CFA Level 2"],
            "skills_mentioned": [],
            "summary": "",
        }
        result = _postprocess_extraction(structured)
        assert result["education_level"] == "Professional"

    def test_treats_none_string_as_missing(self):
        """education_level = 'None' (string) should be treated as missing."""
        structured = {
            "education_level": "None",
            "certifications": [{"name": "CA", "issuer": "ICAI", "date": None}],
            "skills_mentioned": [],
            "summary": "",
        }
        result = _postprocess_extraction(structured)
        assert result["education_level"] == "Professional"


class TestExperienceValidation:
    """Test that invalid experience entries are filtered out."""

    def test_filters_entries_without_company_or_title(self):
        structured = {
            "experience": [
                {"company": "Wipro", "title": "Engineer", "start_date": "2020"},
                {"company": "", "title": "", "start_date": "2019"},
                {"company": None, "title": None, "start_date": "2018"},
                {"company": "Google", "title": "SWE", "start_date": "2017"},
            ],
        }
        result = _postprocess_extraction(structured)
        assert len(result["experience"]) == 2
        assert result["experience"][0]["company"] == "Wipro"
        assert result["experience"][1]["company"] == "Google"

    def test_keeps_entry_with_company_only(self):
        structured = {
            "experience": [
                {"company": "Wipro", "title": None, "start_date": "2020"},
            ],
        }
        result = _postprocess_extraction(structured)
        assert len(result["experience"]) == 1

    def test_filters_non_dict_entries(self):
        structured = {
            "experience": [
                "Some random string",
                {"company": "Valid", "title": "Role"},
                42,
            ],
        }
        result = _postprocess_extraction(structured)
        assert len(result["experience"]) == 1


class TestProfessionalCertKeywords:
    """Test that the cert keyword set is comprehensive."""

    def test_all_major_finance_certs_covered(self):
        expected = {"ca", "aca", "fca", "cpa", "acca", "cfa", "acs", "icwa", "cma"}
        assert expected.issubset(_PROFESSIONAL_CERT_KEYWORDS)

    def test_tech_security_certs_covered(self):
        expected = {"cisa", "cissp"}
        assert expected.issubset(_PROFESSIONAL_CERT_KEYWORDS)
