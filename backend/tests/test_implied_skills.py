"""Tests for implied skill enforcement in the skill pipeline."""

import pytest
from app.services.skill_pipeline import SkillAssessment, _enforce_implied_skills


class TestImpliedSkillEnforcement:
    def _make_assessment(self, name, depth, confidence=0.8, reasoning="Test"):
        return SkillAssessment(
            name=name,
            category="language",
            estimated_depth=depth,
            depth_confidence=confidence,
            depth_reasoning=reasoning,
        )

    def test_react_implies_html_css_js(self):
        assessments = [
            self._make_assessment("react", 4),
            self._make_assessment("html", 2),
            self._make_assessment("css", 1),
            self._make_assessment("javascript", 2),
        ]
        _enforce_implied_skills(assessments)
        html = next(a for a in assessments if a.name == "html")
        css = next(a for a in assessments if a.name == "css")
        js = next(a for a in assessments if a.name == "javascript")
        assert html.estimated_depth >= 3
        assert css.estimated_depth >= 3
        assert js.estimated_depth >= 3

    def test_django_implies_python(self):
        assessments = [
            self._make_assessment("django", 4),
            self._make_assessment("python", 2),
        ]
        _enforce_implied_skills(assessments)
        python = next(a for a in assessments if a.name == "python")
        assert python.estimated_depth >= 3

    def test_no_boost_when_depth_below_3(self):
        assessments = [
            self._make_assessment("react", 2),  # Below threshold
            self._make_assessment("html", 1),
        ]
        _enforce_implied_skills(assessments)
        html = next(a for a in assessments if a.name == "html")
        assert html.estimated_depth == 1  # Should not be boosted

    def test_no_boost_when_already_sufficient(self):
        assessments = [
            self._make_assessment("react", 4),
            self._make_assessment("javascript", 4),  # Already >= 3
        ]
        _enforce_implied_skills(assessments)
        js = next(a for a in assessments if a.name == "javascript")
        assert js.estimated_depth == 4  # Unchanged

    def test_kubernetes_implies_docker_and_linux(self):
        assessments = [
            self._make_assessment("kubernetes", 4),
            self._make_assessment("docker", 1),
            self._make_assessment("linux", 1),
        ]
        _enforce_implied_skills(assessments)
        docker = next(a for a in assessments if a.name == "docker")
        linux = next(a for a in assessments if a.name == "linux")
        assert docker.estimated_depth >= 2
        assert linux.estimated_depth >= 2

    def test_boosts_depth_0_skills(self):
        """If React is depth 4, HTML at depth 0 should be boosted to 3."""
        assessments = [
            self._make_assessment("react", 4),
            self._make_assessment("html", 0),
        ]
        _enforce_implied_skills(assessments)
        html = next(a for a in assessments if a.name == "html")
        assert html.estimated_depth >= 3

    def test_depth_0_boost_has_proper_reasoning(self):
        assessments = [
            self._make_assessment("react", 4),
            self._make_assessment("css", 0, reasoning="Not found on resume."),
        ]
        _enforce_implied_skills(assessments)
        css = next(a for a in assessments if a.name == "css")
        assert "Implied by" in css.depth_reasoning
        assert "react" in css.depth_reasoning.lower()

    def test_does_not_create_missing_skills(self):
        """Implied skill enforcement only boosts existing assessments, doesn't create new ones."""
        assessments = [
            self._make_assessment("react", 4),
            # No html, css, or js assessments
        ]
        _enforce_implied_skills(assessments)
        assert len(assessments) == 1  # Should not add new assessments

    def test_reasoning_updated_on_boost(self):
        assessments = [
            self._make_assessment("fastapi", 4),
            self._make_assessment("python", 2, reasoning="Basic usage"),
        ]
        _enforce_implied_skills(assessments)
        python = next(a for a in assessments if a.name == "python")
        assert "Boosted" in python.depth_reasoning
        assert "fastapi" in python.depth_reasoning.lower()
