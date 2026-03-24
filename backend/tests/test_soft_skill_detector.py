"""Tests for soft skill proxy detection."""

import pytest
from app.services.soft_skill_detector import (
    detect_soft_skill_proxies,
    get_soft_skill_gaps_for_role,
    _deduplicate_evidence,
)


class TestSoftSkillProxyDetection:
    def test_detects_leadership_from_team_management(self):
        parsed = {
            "experience": [
                {
                    "title": "Engineering Manager",
                    "company": "Tech Corp",
                    "description": "Managed a team of 15 engineers across 3 squads.",
                },
            ]
        }
        result = detect_soft_skill_proxies(parsed)
        categories = [e["category"] for e in result["soft_skills"]]
        assert "leadership" in categories

    def test_detects_communication_from_presentations(self):
        parsed = {
            "experience": [
                {
                    "title": "Director",
                    "company": "Acme",
                    "description": "Presented to C-suite executives quarterly on project status.",
                },
            ]
        }
        result = detect_soft_skill_proxies(parsed)
        categories = [e["category"] for e in result["soft_skills"]]
        assert "communication" in categories

    def test_detects_problem_solving_from_metrics(self):
        parsed = {
            "experience": [
                {
                    "title": "Developer",
                    "company": "Startup",
                    "description": "Reduced page load time by 40% through optimization.",
                },
            ]
        }
        result = detect_soft_skill_proxies(parsed)
        categories = [e["category"] for e in result["soft_skills"]]
        assert "problem_solving" in categories

    def test_detects_collaboration(self):
        parsed = {
            "experience": [
                {
                    "title": "Engineer",
                    "company": "Corp",
                    "description": "Partnered with product and design teams on feature delivery.",
                },
            ]
        }
        result = detect_soft_skill_proxies(parsed)
        categories = [e["category"] for e in result["soft_skills"]]
        assert "collaboration" in categories

    def test_detects_strategic_thinking(self):
        parsed = {
            "experience": [
                {
                    "title": "VP Engineering",
                    "company": "BigCo",
                    "description": "Developed the technology strategy and roadmap for 2024.",
                },
            ]
        }
        result = detect_soft_skill_proxies(parsed)
        categories = [e["category"] for e in result["soft_skills"]]
        assert "strategic_thinking" in categories

    def test_empty_resume_returns_zero_score(self):
        result = detect_soft_skill_proxies({})
        assert result["soft_skill_score"] == 0
        assert result["soft_skills"] == []

    def test_mba_detected_as_leadership_signal(self):
        parsed = {
            "experience": [],
            "education": [
                {"degree": "MBA", "institution": "Harvard Business School"},
            ],
        }
        result = detect_soft_skill_proxies(parsed)
        categories = [e["category"] for e in result["soft_skills"]]
        assert "leadership" in categories

    def test_result_has_required_fields(self):
        parsed = {"experience": [{"title": "Dev", "description": "Built stuff."}]}
        result = detect_soft_skill_proxies(parsed)
        assert "soft_skills" in result
        assert "summary" in result
        assert "soft_skill_score" in result
        assert "strongest_areas" in result
        assert "weakest_areas" in result

    def test_score_is_0_to_100(self):
        parsed = {
            "experience": [
                {
                    "title": "CTO",
                    "company": "Unicorn",
                    "description": (
                        "Led team of 50 engineers. Presented to board quarterly. "
                        "Reduced costs by 30%. Partnered with sales and marketing. "
                        "Developed 3-year technology roadmap."
                    ),
                },
            ]
        }
        result = detect_soft_skill_proxies(parsed)
        assert 0 <= result["soft_skill_score"] <= 100


class TestSoftSkillGaps:
    def test_leadership_gap_for_manager_role(self):
        soft_result = {
            "summary": {
                "leadership": {"count": 0, "max_strength": 0.0, "evidence": []},
                "communication": {"count": 1, "max_strength": 0.7, "evidence": []},
                "problem_solving": {"count": 0, "max_strength": 0.0, "evidence": []},
                "collaboration": {"count": 0, "max_strength": 0.0, "evidence": []},
                "strategic_thinking": {"count": 0, "max_strength": 0.0, "evidence": []},
            }
        }
        gaps = get_soft_skill_gaps_for_role(soft_result, "Engineering Manager", "hybrid")
        gap_categories = [g["category"] for g in gaps]
        assert "leadership" in gap_categories

    def test_no_gaps_when_evidence_present(self):
        soft_result = {
            "summary": {
                "leadership": {"count": 3, "max_strength": 0.9, "evidence": ["Led team"]},
                "communication": {"count": 2, "max_strength": 0.8, "evidence": ["Presented"]},
                "problem_solving": {"count": 1, "max_strength": 0.7, "evidence": []},
                "collaboration": {"count": 1, "max_strength": 0.7, "evidence": []},
                "strategic_thinking": {"count": 2, "max_strength": 0.8, "evidence": []},
            }
        }
        gaps = get_soft_skill_gaps_for_role(soft_result, "Director of Engineering", "hybrid")
        assert len(gaps) == 0


class TestDeduplication:
    def test_removes_exact_duplicates(self):
        evidence = [
            {"category": "leadership", "matched_text": "managed team of 10"},
            {"category": "leadership", "matched_text": "managed team of 10"},
        ]
        result = _deduplicate_evidence(evidence)
        assert len(result) == 1

    def test_keeps_different_categories(self):
        evidence = [
            {"category": "leadership", "matched_text": "managed team"},
            {"category": "communication", "matched_text": "presented to executives"},
        ]
        result = _deduplicate_evidence(evidence)
        assert len(result) == 2
