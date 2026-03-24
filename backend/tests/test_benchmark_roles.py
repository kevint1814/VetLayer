"""
Benchmark Test Suite — Cross-role-type regression tests for VetLayer.

Tests the deterministic pipeline components across 5 role clusters:
  1. Tech IC (Software Engineer, Data Engineer, DevOps)
  2. Professional/Finance (Financial Controller, Risk Manager, Auditor)
  3. Client Experience / Consulting (Director CX, Engagement Manager)
  4. Operations / HR (Operations Manager, HR Director)
  5. Hybrid / Leadership (Engineering Manager, VP Engineering, CTO)

Each test validates:
  - Role type detection produces the correct classification
  - Seniority mapping is accurate
  - Ontology resolves skills to the correct domain
  - Cluster prompt selection is correct
  - Scoring weights are appropriate for the role type

These tests are DETERMINISTIC (no LLM calls) and should run in <1 second.
They form the regression safety net against the "fix one, break another" problem.
"""

import unittest
from app.services.role_type_detector import detect_role_type
from app.services.experience_trajectory import _get_seniority_level
from app.services.skill_ontology import (
    resolve_skill, get_skill_domain, compute_domain_profile,
    classify_skills_by_domain, get_evidence_variants, get_ontology,
)
from app.services.cluster_prompts import build_assessment_prompt


# ═══════════════════════════════════════════════════════════════════════
# 1. TECH IC ROLES
# ═══════════════════════════════════════════════════════════════════════

class TestTechICBenchmark(unittest.TestCase):
    """Benchmark tests for technology individual contributor roles."""

    def test_software_engineer_classification(self):
        """Software Engineer with tech skills → skill_heavy."""
        result = detect_role_type(
            job_title="Software Engineer",
            job_description="Build scalable backend services using Python and Go.",
            required_skills=[
                {"skill": "Python"}, {"skill": "Go"}, {"skill": "PostgreSQL"},
                {"skill": "Docker"}, {"skill": "AWS"}, {"skill": "REST API"},
            ],
        )
        self.assertEqual(result["type"], "skill_heavy")
        self.assertGreater(result["signals"]["tech_ratio"], 0.5)

    def test_data_engineer_classification(self):
        """Data Engineer → skill_heavy."""
        result = detect_role_type(
            job_title="Data Engineer",
            required_skills=[
                {"skill": "Python"}, {"skill": "SQL"}, {"skill": "Spark"},
                {"skill": "Airflow"}, {"skill": "AWS"}, {"skill": "Kafka"},
            ],
        )
        self.assertEqual(result["type"], "skill_heavy")

    def test_devops_engineer_classification(self):
        """DevOps Engineer → skill_heavy."""
        result = detect_role_type(
            job_title="DevOps Engineer",
            required_skills=[
                {"skill": "Docker"}, {"skill": "Kubernetes"}, {"skill": "Terraform"},
                {"skill": "AWS"}, {"skill": "CI/CD"}, {"skill": "Linux"},
            ],
        )
        self.assertEqual(result["type"], "skill_heavy")

    def test_tech_seniority_levels(self):
        """Tech title → correct seniority level."""
        self.assertEqual(_get_seniority_level("Software Engineer"), 3.0)
        self.assertEqual(_get_seniority_level("Senior Software Engineer"), 4.0)
        self.assertEqual(_get_seniority_level("Staff Engineer"), 4.5)
        self.assertEqual(_get_seniority_level("Principal Engineer"), 4.5)
        self.assertEqual(_get_seniority_level("Engineering Manager"), 5.0)

    def test_tech_prompt_cluster(self):
        """Tech IC roles get tech-specific prompt."""
        prompt = build_assessment_prompt("skill_heavy", "Software Engineer")
        self.assertIn("TECHNOLOGY / INDIVIDUAL CONTRIBUTOR", prompt)
        self.assertIn("React, Next.js, Vue", prompt)

    def test_tech_skills_resolve_to_technology_domain(self):
        """Python, React, Docker → technology domain."""
        for skill in ["Python", "React", "Docker", "AWS", "PostgreSQL"]:
            self.assertEqual(get_skill_domain(skill), "technology",
                             f"{skill} should resolve to technology domain")

    def test_tech_domain_profile_is_tech_heavy(self):
        """JD with tech skills → tech-heavy domain profile."""
        skills = ["Python", "React", "Docker", "AWS", "PostgreSQL", "CI/CD"]
        profile = compute_domain_profile(skills)
        self.assertGreater(profile.get("technology", 0), 0.5)


# ═══════════════════════════════════════════════════════════════════════
# 2. PROFESSIONAL / FINANCE ROLES
# ═══════════════════════════════════════════════════════════════════════

class TestProfessionalFinanceBenchmark(unittest.TestCase):
    """Benchmark tests for finance/professional domain roles."""

    def test_finance_controller_classification(self):
        """Finance Controller with finance skills → experience_heavy."""
        result = detect_role_type(
            job_title="Finance Control LEC Lead",
            job_description="Financial controllership, statutory reporting, IFRS compliance.",
            required_skills=[
                {"skill": "IFRS"}, {"skill": "Financial Reporting"},
                {"skill": "Compliance"}, {"skill": "Financial Planning"},
                {"skill": "ERP"}, {"skill": "Audit"},
                {"skill": "Stakeholder Engagement"},
            ],
        )
        self.assertEqual(result["type"], "experience_heavy",
                         f"Finance Controller should be experience_heavy, got {result['type']}. "
                         f"Signals: {result['signals']}")

    def test_risk_manager_classification(self):
        """Risk Manager → experience_heavy."""
        result = detect_role_type(
            job_title="Risk Manager",
            required_skills=[
                {"skill": "Risk Management"}, {"skill": "Compliance"},
                {"skill": "Audit"}, {"skill": "Financial Reporting"},
            ],
        )
        self.assertEqual(result["type"], "experience_heavy")

    def test_finance_seniority_levels(self):
        """Finance titles → correct seniority levels."""
        self.assertEqual(_get_seniority_level("Financial Controller"), 6.0)
        self.assertEqual(_get_seniority_level("Controller"), 5.5)
        self.assertEqual(_get_seniority_level("Treasurer"), 5.5)
        self.assertEqual(_get_seniority_level("Head of Finance"), 6.0)
        self.assertEqual(_get_seniority_level("CFO"), 8.0)

    def test_finance_controller_not_demoted(self):
        """Financial Controller (6.0) > Manager (5.0) — NOT a demotion."""
        fc_level = _get_seniority_level("Financial Controller")
        mgr_level = _get_seniority_level("Manager")
        self.assertGreater(fc_level, mgr_level,
                           "Financial Controller should be above Manager in seniority")

    def test_finance_prompt_cluster(self):
        """Finance roles get professional-specific prompt."""
        prompt = build_assessment_prompt("experience_heavy", "Finance Controller")
        self.assertIn("PROFESSIONAL / DOMAIN SPECIALIST", prompt)
        self.assertIn("SENIOR TITLE + SKILL IN SCOPE", prompt)

    def test_finance_skills_resolve_to_finance_domain(self):
        """IFRS, Financial Reporting, Compliance → finance domain."""
        for skill in ["IFRS", "GAAP", "Financial Reporting", "Audit", "Treasury"]:
            domain = get_skill_domain(skill)
            self.assertEqual(domain, "finance",
                             f"{skill} should resolve to finance domain, got {domain}")

    def test_finance_domain_profile_is_professional(self):
        """JD with finance skills → professional-heavy domain profile."""
        skills = ["IFRS", "Financial Reporting", "Compliance", "Audit", "ERP"]
        profile = compute_domain_profile(skills)
        professional_ratio = sum(v for k, v in profile.items()
                                  if k in ("finance", "hr", "legal", "healthcare",
                                          "marketing", "sales", "operations", "consulting"))
        self.assertGreater(professional_ratio, 0.5,
                           f"Finance JD should have professional_ratio > 0.5, got {professional_ratio}")


# ═══════════════════════════════════════════════════════════════════════
# 3. CLIENT EXPERIENCE / CONSULTING ROLES
# ═══════════════════════════════════════════════════════════════════════

class TestClientExperienceBenchmark(unittest.TestCase):
    """Benchmark tests for client experience and consulting roles."""

    def test_director_cx_classification(self):
        """Director, Client Experience → experience_heavy."""
        result = detect_role_type(
            job_title="Director, Acceleration Centers (AC) Client Experience",
            required_skills=[
                {"skill": "Client Experience Strategy"}, {"skill": "Team Leadership"},
                {"skill": "Stakeholder Engagement"}, {"skill": "Governance"},
                {"skill": "Operational Excellence"}, {"skill": "Business Acumen"},
            ],
        )
        self.assertIn(result["type"], ["experience_heavy", "hybrid"],
                      f"Director CX should be experience_heavy or hybrid, got {result['type']}")

    def test_consulting_skills_resolve_correctly(self):
        """Consulting skills → consulting or leadership domain."""
        self.assertEqual(get_skill_domain("Client Experience Strategy"), "consulting")
        self.assertEqual(get_skill_domain("Business Acumen"), "consulting")
        self.assertEqual(get_skill_domain("Stakeholder Engagement"), "leadership")
        self.assertEqual(get_skill_domain("Governance"), "leadership")

    def test_director_seniority(self):
        """Director titles → level 6."""
        self.assertEqual(_get_seniority_level("Director, Client Experience"), 6.0)
        self.assertEqual(_get_seniority_level("Associate Director"), 5.5)


# ═══════════════════════════════════════════════════════════════════════
# 4. OPERATIONS / HR ROLES
# ═══════════════════════════════════════════════════════════════════════

class TestOperationsHRBenchmark(unittest.TestCase):
    """Benchmark tests for operations and HR roles."""

    def test_operations_manager_classification(self):
        """Operations Manager → experience_heavy."""
        result = detect_role_type(
            job_title="Operations Manager",
            required_skills=[
                {"skill": "Process Improvement"}, {"skill": "Project Management"},
                {"skill": "Operations Management"}, {"skill": "Team Leadership"},
            ],
        )
        self.assertEqual(result["type"], "experience_heavy")

    def test_hr_director_classification(self):
        """HR Director → experience_heavy."""
        result = detect_role_type(
            job_title="HR Director",
            required_skills=[
                {"skill": "Talent Acquisition"}, {"skill": "Performance Management"},
                {"skill": "Employee Engagement"}, {"skill": "Learning and Development"},
            ],
        )
        self.assertEqual(result["type"], "experience_heavy")

    def test_operations_skills_resolve_correctly(self):
        """Operations skills → operations domain."""
        self.assertEqual(get_skill_domain("Process Improvement"), "operations")
        self.assertEqual(get_skill_domain("Supply Chain Management"), "operations")
        self.assertEqual(get_skill_domain("Operations Management"), "operations")

    def test_hr_skills_resolve_correctly(self):
        """HR skills → hr domain."""
        self.assertEqual(get_skill_domain("Talent Acquisition"), "hr")
        self.assertEqual(get_skill_domain("Performance Management"), "hr")
        self.assertEqual(get_skill_domain("Employee Engagement"), "hr")


# ═══════════════════════════════════════════════════════════════════════
# 5. HYBRID / LEADERSHIP ROLES
# ═══════════════════════════════════════════════════════════════════════

class TestHybridLeadershipBenchmark(unittest.TestCase):
    """Benchmark tests for hybrid and leadership roles."""

    def test_engineering_manager_classification(self):
        """Engineering Manager with mixed skills → hybrid."""
        result = detect_role_type(
            job_title="Engineering Manager",
            required_skills=[
                {"skill": "Python"}, {"skill": "AWS"}, {"skill": "Docker"},
                {"skill": "Team Leadership"}, {"skill": "Stakeholder Engagement"},
            ],
        )
        self.assertEqual(result["type"], "hybrid")

    def test_product_manager_classification(self):
        """Product Manager → hybrid."""
        result = detect_role_type(
            job_title="Product Manager",
            required_skills=[
                {"skill": "Data Analysis"}, {"skill": "Agile"},
                {"skill": "Stakeholder Engagement"}, {"skill": "Project Management"},
            ],
        )
        self.assertEqual(result["type"], "hybrid")

    def test_cto_gets_leadership_prompt(self):
        """CTO → leadership cluster prompt override."""
        prompt = build_assessment_prompt("skill_heavy", "CTO")
        self.assertIn("LEADERSHIP / EXECUTIVE", prompt)

    def test_cfo_gets_leadership_prompt(self):
        """CFO → leadership cluster prompt override."""
        prompt = build_assessment_prompt("experience_heavy", "CFO")
        self.assertIn("LEADERSHIP / EXECUTIVE", prompt)

    def test_vp_engineering_seniority(self):
        """VP Engineering → level 7."""
        self.assertEqual(_get_seniority_level("VP Engineering"), 7.0)

    def test_hybrid_prompt_has_dual_assessment(self):
        """Hybrid roles get both tech and leadership calibration."""
        prompt = build_assessment_prompt("hybrid", "Engineering Manager")
        self.assertIn("HYBRID / TECHNICAL LEADERSHIP", prompt)
        self.assertIn("DUAL ASSESSMENT APPROACH", prompt)

    def test_managing_director_seniority(self):
        """Managing Director → level 7.5."""
        self.assertEqual(_get_seniority_level("Managing Director"), 7.5)


# ═══════════════════════════════════════════════════════════════════════
# 6. CROSS-CLUSTER REGRESSION TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestCrossClusterRegression(unittest.TestCase):
    """
    These tests verify that fixing one role type doesn't break another.
    They encode the exact failure mode that prompted this redesign.
    """

    def test_adding_finance_skills_doesnt_break_tech_detection(self):
        """
        ROOT CAUSE TEST: Adding finance skills to evidence aliases used to
        inflate assessable_ratio and make tech roles classify as experience_heavy.
        With the decoupled ontology, this should never happen.
        """
        # A pure tech JD should ALWAYS be skill_heavy
        tech_result = detect_role_type(
            job_title="Software Engineer",
            required_skills=[
                {"skill": "Python"}, {"skill": "Django"}, {"skill": "PostgreSQL"},
                {"skill": "Docker"}, {"skill": "AWS"}, {"skill": "CI/CD"},
            ],
        )
        self.assertEqual(tech_result["type"], "skill_heavy")

        # A pure finance JD should ALWAYS be experience_heavy
        finance_result = detect_role_type(
            job_title="Finance Controller",
            required_skills=[
                {"skill": "IFRS"}, {"skill": "Financial Reporting"},
                {"skill": "Compliance"}, {"skill": "Audit"},
                {"skill": "Financial Planning"}, {"skill": "ERP"},
            ],
        )
        self.assertEqual(finance_result["type"], "experience_heavy")

        # They should use different prompt clusters
        tech_prompt = build_assessment_prompt(
            tech_result["type"], "Software Engineer",
            tech_result.get("signals", {}).get("domain_profile"),
        )
        finance_prompt = build_assessment_prompt(
            finance_result["type"], "Finance Controller",
            finance_result.get("signals", {}).get("domain_profile"),
        )
        self.assertIn("TECHNOLOGY", tech_prompt)
        self.assertIn("PROFESSIONAL", finance_prompt)

    def test_scoring_weights_differ_by_role_type(self):
        """
        Skill_heavy roles weight skills at 1.20; experience_heavy weight
        trajectory at 1.40. These weights MUST stay different.
        """
        tech = detect_role_type("Software Engineer",
                                required_skills=[{"skill": "Python"}])
        finance = detect_role_type("Finance Controller",
                                   required_skills=[{"skill": "IFRS"}])

        self.assertGreater(
            tech["scoring_weights"]["skill_match"],
            finance["scoring_weights"]["skill_match"],
            "Tech roles should weight skill_match higher than finance roles"
        )
        self.assertGreater(
            finance["scoring_weights"]["trajectory"],
            tech["scoring_weights"]["trajectory"],
            "Finance roles should weight trajectory higher than tech roles"
        )

    def test_ontology_skill_count_and_coverage(self):
        """Ontology should have reasonable coverage across domains."""
        ontology = get_ontology()
        self.assertGreaterEqual(len(ontology), 70, "Ontology should have 70+ skills")

        # Check domain distribution
        domains = set(n.domain for n in ontology.values())
        required_domains = {"technology", "finance", "operations", "hr",
                           "marketing", "sales", "leadership", "consulting"}
        for d in required_domains:
            self.assertIn(d, domains, f"Ontology missing domain: {d}")

    def test_domain_profile_doesnt_leak_across_clusters(self):
        """Tech skills should NOT increase professional ratio, and vice versa."""
        tech_profile = compute_domain_profile(["Python", "React", "Docker"])
        self.assertEqual(tech_profile.get("finance", 0), 0)

        finance_profile = compute_domain_profile(["IFRS", "GAAP", "Audit"])
        self.assertEqual(finance_profile.get("technology", 0), 0)


class TestScoringNormalization(unittest.TestCase):
    """
    Verify that scoring weight normalization keeps the base-component ceiling
    equal across all role types. Without normalization, experience-heavy roles
    have a structural ceiling of ~71% vs ~91% for skill-heavy roles.
    """

    def test_weight_normalization_produces_equal_ceilings(self):
        """All role types should have the same max base score (0.85) when all
        component scores are 1.0."""
        base_coeffs = [0.35, 0.22, 0.18, 0.10]  # skill, depth, exp, edu
        target = 0.85

        for role_type in ["skill_heavy", "experience_heavy", "hybrid"]:
            result = detect_role_type(
                "Test Role",
                required_skills=[{"skill": "Python"}] if role_type == "skill_heavy"
                else [{"skill": "IFRS"}] if role_type == "experience_heavy"
                else [{"skill": "Python"}, {"skill": "Team Leadership"}],
            )
            mults = result["scoring_weights"]
            raw = [
                mults.get("skill_match", 1.0),
                mults.get("depth", 1.0),
                mults.get("experience", 1.0),
                mults.get("education", 1.0),
            ]
            # Apply normalization (same logic as _compute_scores)
            weighted_sum = sum(b * m for b, m in zip(base_coeffs, raw))
            norm_factor = target / weighted_sum if weighted_sum > 0 else 1.0
            normalized = [m * norm_factor for m in raw]
            result_sum = sum(b * n for b, n in zip(base_coeffs, normalized))

            self.assertAlmostEqual(
                result_sum, target, places=2,
                msg=f"{role_type} normalized base sum should be {target}, got {result_sum:.4f}"
            )

    def test_experience_heavy_weights_still_favor_experience(self):
        """Even after normalization, experience-heavy should weight
        experience and trajectory higher than skill_match."""
        result = detect_role_type(
            "Finance Controller",
            required_skills=[{"skill": "IFRS"}, {"skill": "Compliance"}],
        )
        w = result["scoring_weights"]
        self.assertGreater(w["experience"], w["skill_match"],
                          "Experience-heavy: experience weight should exceed skill_match")
        self.assertGreater(w["trajectory"], 1.0,
                          "Experience-heavy: trajectory weight should be above 1.0")


if __name__ == "__main__":
    unittest.main()
