"""Tests for role type detector — classifying jobs as skill-heavy, hybrid, or experience-heavy."""

import pytest
from app.services.role_type_detector import detect_role_type, _is_soft_skill, _is_non_tech_hard_skill


class TestRoleTypeDetection:
    """Test role type classification for various job types."""

    def test_software_engineer_is_skill_heavy(self):
        result = detect_role_type(
            job_title="Senior Software Engineer",
            required_skills=[
                {"skill": "Python", "min_depth": 4},
                {"skill": "React", "min_depth": 3},
                {"skill": "PostgreSQL", "min_depth": 3},
                {"skill": "Docker", "min_depth": 2},
                {"skill": "AWS", "min_depth": 3},
            ],
        )
        assert result["type"] == "skill_heavy"
        assert result["confidence"] > 0.3

    def test_data_engineer_is_skill_heavy(self):
        result = detect_role_type(
            job_title="Data Engineer",
            required_skills=[
                {"skill": "Python", "min_depth": 3},
                {"skill": "SQL", "min_depth": 4},
                {"skill": "Spark", "min_depth": 3},
                {"skill": "Airflow", "min_depth": 2},
            ],
        )
        assert result["type"] == "skill_heavy"

    def test_hr_manager_is_experience_heavy(self):
        result = detect_role_type(
            job_title="HR Manager",
            required_skills=[
                {"skill": "Communication", "min_depth": 3},
                {"skill": "Leadership", "min_depth": 3},
                {"skill": "Employee Relations", "min_depth": 3},
                {"skill": "Performance Management", "min_depth": 3},
            ],
        )
        assert result["type"] == "experience_heavy"

    def test_marketing_director_is_experience_heavy(self):
        result = detect_role_type(
            job_title="Marketing Director",
            required_skills=[
                {"skill": "Brand Management", "min_depth": 3},
                {"skill": "Leadership", "min_depth": 4},
                {"skill": "Strategic Thinking", "min_depth": 3},
                {"skill": "Communication", "min_depth": 3},
            ],
        )
        assert result["type"] == "experience_heavy"

    def test_engineering_manager_is_hybrid(self):
        result = detect_role_type(
            job_title="Engineering Manager",
            required_skills=[
                {"skill": "Python", "min_depth": 3},
                {"skill": "System Design", "min_depth": 4},
                {"skill": "Leadership", "min_depth": 3},
                {"skill": "Agile", "min_depth": 2},
            ],
        )
        assert result["type"] in ("hybrid", "skill_heavy")

    def test_product_manager_is_hybrid(self):
        result = detect_role_type(
            job_title="Product Manager",
            required_skills=[
                {"skill": "Agile", "min_depth": 3},
                {"skill": "SQL", "min_depth": 2},
                {"skill": "Stakeholder Management", "min_depth": 3},
                {"skill": "Communication", "min_depth": 3},
            ],
        )
        assert result["type"] in ("hybrid", "experience_heavy")

    def test_sales_executive_is_experience_heavy(self):
        result = detect_role_type(
            job_title="Sales Executive",
            required_skills=[
                {"skill": "Negotiation", "min_depth": 3},
                {"skill": "Communication", "min_depth": 3},
                {"skill": "CRM", "min_depth": 2},
                {"skill": "Relationship Building", "min_depth": 3},
            ],
        )
        assert result["type"] == "experience_heavy"

    def test_empty_skills_defaults_to_title(self):
        result = detect_role_type(
            job_title="Frontend Developer",
            required_skills=[],
        )
        assert result["type"] == "skill_heavy"

    def test_result_has_required_fields(self):
        result = detect_role_type(
            job_title="Software Engineer",
            required_skills=[{"skill": "Python"}],
        )
        assert "type" in result
        assert "confidence" in result
        assert "signals" in result
        assert "scoring_weights" in result
        assert result["type"] in ("skill_heavy", "hybrid", "experience_heavy")

    def test_scoring_weights_vary_by_type(self):
        tech = detect_role_type("Software Engineer", required_skills=[{"skill": "Python"}])
        hr = detect_role_type("HR Manager", required_skills=[{"skill": "Communication"}])

        tech_weights = tech["scoring_weights"]
        hr_weights = hr["scoring_weights"]

        # Tech should weight skills higher
        assert tech_weights["skill_match"] > hr_weights["skill_match"]
        # HR should weight trajectory higher
        assert hr_weights["trajectory"] > tech_weights["trajectory"]


class TestSoftSkillClassification:
    def test_communication_is_soft_skill(self):
        assert _is_soft_skill("Communication") is True

    def test_leadership_is_soft_skill(self):
        assert _is_soft_skill("Leadership") is True

    def test_python_is_not_soft_skill(self):
        assert _is_soft_skill("Python") is False

    def test_teamwork_is_soft_skill(self):
        assert _is_soft_skill("teamwork") is True

    def test_react_is_not_soft_skill(self):
        assert _is_soft_skill("React") is False


class TestNonTechHardSkill:
    def test_seo_is_non_tech(self):
        assert _is_non_tech_hard_skill("SEO") is True

    def test_financial_modeling_is_non_tech(self):
        assert _is_non_tech_hard_skill("Financial Modeling") is True

    def test_python_is_not_non_tech(self):
        assert _is_non_tech_hard_skill("Python") is False

    def test_media_buying_is_non_tech(self):
        assert _is_non_tech_hard_skill("Media Buying") is True
