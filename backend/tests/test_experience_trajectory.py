"""Tests for experience trajectory scoring."""

import pytest
from app.services.experience_trajectory import (
    analyze_trajectory,
    _get_seniority_level,
    _classify_progression,
    _date_to_months,
    _compute_total_years,
    _analyze_gaps,
)


class TestSeniorityLevelMapping:
    def test_intern_is_level_1(self):
        assert _get_seniority_level("Software Engineering Intern") == 1

    def test_junior_is_level_2(self):
        assert _get_seniority_level("Junior Developer") == 2

    def test_default_is_mid_level(self):
        assert _get_seniority_level("Software Developer") == 3.0

    def test_senior_is_level_4(self):
        assert _get_seniority_level("Senior Software Engineer") == 4

    def test_manager_is_level_5(self):
        assert _get_seniority_level("Engineering Manager") == 5

    def test_director_is_level_6(self):
        assert _get_seniority_level("Director of Engineering") == 6

    def test_vp_is_level_7(self):
        assert _get_seniority_level("Vice President of Technology") == 7

    def test_cto_is_level_8(self):
        assert _get_seniority_level("CTO") == 8

    def test_team_lead_is_level_4(self):
        assert _get_seniority_level("Team Lead") == 4


class TestDateParsing:
    def test_month_year_format(self):
        result = _date_to_months("Jan 2020")
        assert result == 2020 * 12 + 1

    def test_full_month_year(self):
        result = _date_to_months("January 2020")
        assert result == 2020 * 12 + 1

    def test_just_year(self):
        result = _date_to_months("2020")
        assert result == 2020 * 12 + 6  # Mid-year assumption

    def test_mm_yyyy(self):
        result = _date_to_months("03/2020")
        assert result == 2020 * 12 + 3

    def test_empty_returns_none(self):
        assert _date_to_months("") is None

    def test_invalid_returns_none(self):
        assert _date_to_months("xyz") is None


    def test_yyyy_mm_format(self):
        """YYYY-MM is the most common format from VetLayer's resume parser."""
        assert _date_to_months("2022-09") == 2022 * 12 + 9
        assert _date_to_months("2004-11") == 2004 * 12 + 11
        assert _date_to_months("2014-02") == 2014 * 12 + 2
        assert _date_to_months("2008-01") == 2008 * 12 + 1

    def test_yyyy_mm_slash_format(self):
        assert _date_to_months("2022/09") == 2022 * 12 + 9

    def test_mm_dash_yyyy_format(self):
        assert _date_to_months("09-2022") == 2022 * 12 + 9


class TestNoPhantomGaps:
    """Regression test: YYYY-MM dates must not produce phantom gaps."""

    def test_continuous_career_no_gaps(self):
        """Deepaka Tulupule career: seamless transitions, zero gaps."""
        exps = [
            {"start_months": 2002*12+1, "end_months": 2003*12+6},   # BPO India
            {"start_months": 2003*12+6, "end_months": 2004*12+3},   # Accenture
            {"start_months": 2004*12+6, "end_months": 2004*12+11},  # ACS
            {"start_months": 2004*12+11, "end_months": 2008*12+1},  # MphasiS
            {"start_months": 2008*12+1, "end_months": 2009*12+1},   # Applied Materials
            {"start_months": 2009*12+2, "end_months": 2014*12+1},   # MphasiS AVP
            {"start_months": 2014*12+2, "end_months": 2022*12+8},   # XLHealth
            {"start_months": 2022*12+9, "end_months": 2025*12+9},   # Quess Corp
        ]
        gap_months, gap_count = _analyze_gaps(exps)
        assert gap_count == 0, f"Expected 0 gaps but found {gap_count} ({gap_months} months)"
        assert gap_months == 0

    def test_yyyy_mm_dates_parsed_correctly_in_full_trajectory(self):
        """End-to-end: YYYY-MM dates must produce correct gap analysis."""
        parsed_resume = {
            "experience": [
                {"title": "Account Assistant", "company": "Accenture",
                 "start_date": "2003-06", "end_date": "2004-03"},
                {"title": "Analyst, Finance", "company": "ACS",
                 "start_date": "2004-06", "end_date": "2004-11"},
                {"title": "Finance Manager", "company": "MphasiS",
                 "start_date": "2004-11", "end_date": "2008-01"},
                {"title": "Finance Manager", "company": "Applied Materials",
                 "start_date": "2008-01", "end_date": "2009-01"},
                {"title": "AVP Finance", "company": "MphasiS",
                 "start_date": "2009-02", "end_date": "2014-01"},
                {"title": "Director Finance", "company": "XLHealth",
                 "start_date": "2014-02", "end_date": "2022-08"},
                {"title": "Business CFO", "company": "Quess Corp",
                 "start_date": "2022-09", "end_date": "2025-09"},
            ]
        }
        result = analyze_trajectory(parsed_resume, "Finance Controller")
        assert result["gap_count"] == 0, f"Expected 0 gaps but got {result['gap_count']} ({result['gap_months']} months)"
        assert result["gap_months"] == 0
        assert result["progression_type"] == "ascending"


class TestProgressionClassification:
    def test_ascending_progression(self):
        exps = [
            {"title": "Junior Developer", "start_months": 2018*12, "end_months": 2020*12},
            {"title": "Software Engineer", "start_months": 2020*12, "end_months": 2022*12},
            {"title": "Senior Engineer", "start_months": 2022*12, "end_months": 2024*12},
        ]
        assert _classify_progression(exps) == "ascending"

    def test_lateral_progression(self):
        exps = [
            {"title": "Software Engineer", "start_months": 2018*12, "end_months": 2020*12},
            {"title": "Software Developer", "start_months": 2020*12, "end_months": 2022*12},
            {"title": "Software Engineer", "start_months": 2022*12, "end_months": 2024*12},
        ]
        assert _classify_progression(exps) == "lateral"

    def test_single_role_is_early_career(self):
        exps = [
            {"title": "Developer", "start_months": 2022*12, "end_months": 2024*12},
        ]
        assert _classify_progression(exps) == "early_career"


class TestGapAnalysis:
    def test_no_gaps(self):
        exps = [
            {"start_months": 2020*12, "end_months": 2022*12},
            {"start_months": 2022*12, "end_months": 2024*12},
        ]
        gap_months, gap_count = _analyze_gaps(exps)
        assert gap_count == 0

    def test_detects_gap(self):
        exps = [
            {"start_months": 2018*12, "end_months": 2020*12},
            {"start_months": 2021*12, "end_months": 2024*12},  # 12-month gap
        ]
        gap_months, gap_count = _analyze_gaps(exps)
        assert gap_count == 1
        assert gap_months == 12

    def test_ignores_small_gaps(self):
        exps = [
            {"start_months": 2020*12, "end_months": 2022*12},
            {"start_months": 2022*12+2, "end_months": 2024*12},  # 2-month gap (< 3)
        ]
        gap_months, gap_count = _analyze_gaps(exps)
        assert gap_count == 0


class TestTotalYears:
    def test_simple_case(self):
        exps = [
            {"start_months": 2020*12, "end_months": 2024*12},
        ]
        assert _compute_total_years(exps) == pytest.approx(4.0, abs=0.1)

    def test_overlapping_roles_not_double_counted(self):
        exps = [
            {"start_months": 2020*12, "end_months": 2024*12},
            {"start_months": 2022*12, "end_months": 2025*12},  # Overlaps 2 years
        ]
        total = _compute_total_years(exps)
        # Should be 5 years, not 7
        assert total == pytest.approx(5.0, abs=0.1)


class TestFullTrajectoryAnalysis:
    def test_ascending_career(self):
        parsed_resume = {
            "experience": [
                {
                    "title": "Junior Developer",
                    "company": "Startup Inc",
                    "start_date": "Jan 2016",
                    "end_date": "Dec 2018",
                    "description": "Built web applications.",
                },
                {
                    "title": "Software Engineer",
                    "company": "Tech Corp",
                    "start_date": "Jan 2019",
                    "end_date": "Dec 2021",
                    "description": "Led feature development.",
                },
                {
                    "title": "Senior Software Engineer",
                    "company": "Big Tech",
                    "start_date": "Jan 2022",
                    "end_date": "Present",
                    "description": "Architected microservices.",
                },
            ]
        }
        result = analyze_trajectory(parsed_resume, "Staff Engineer")
        assert result["trajectory_score"] > 40
        assert result["progression_type"] == "ascending"
        assert result["growth_rate"] > 0
        assert result["total_years"] > 7
        assert result["role_count"] == 3

    def test_empty_resume(self):
        result = analyze_trajectory({})
        assert result["trajectory_score"] == 0
        assert result["progression_type"] == "unknown"

    def test_single_role_early_career(self):
        parsed_resume = {
            "experience": [
                {
                    "title": "Developer",
                    "company": "Acme",
                    "start_date": "2023",
                    "end_date": "Present",
                },
            ]
        }
        result = analyze_trajectory(parsed_resume)
        assert result["progression_type"] == "early_career"
        assert result["role_count"] == 1

    def test_result_has_all_fields(self):
        parsed_resume = {
            "experience": [
                {
                    "title": "Manager",
                    "company": "Corp",
                    "start_date": "2020",
                    "end_date": "2024",
                },
            ]
        }
        result = analyze_trajectory(parsed_resume)
        required_keys = [
            "trajectory_score", "growth_rate", "total_years", "gap_months",
            "gap_count", "progression_type", "current_seniority", "peak_seniority",
            "industry_consistency", "industry_match", "company_tier_score",
            "trajectory_summary", "role_count",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"


class TestSeniorityLevelRegression:
    """Regression: _get_seniority_level must handle below-mid titles correctly.

    Previously, Phase 3 used max(best_level=3.0, level) which swallowed
    analyst (2.5), associate (2.5), coordinator (2.5) — they all returned 3.0.
    Also 'assistant' was missing from all phases entirely.
    """

    def test_assistant_is_level_2(self):
        assert _get_seniority_level("Account Assistant") == 2

    def test_analyst_is_below_mid(self):
        assert _get_seniority_level("Analyst, Finance") == 2.5

    def test_associate_is_below_mid(self):
        assert _get_seniority_level("Associate Engineer") == 2.5

    def test_coordinator_is_below_mid(self):
        assert _get_seniority_level("Project Coordinator") == 2.5

    def test_finance_manager_is_manager_level(self):
        assert _get_seniority_level("Finance Manager") == 5

    def test_avp_is_above_director(self):
        assert _get_seniority_level("AVP Finance") == 6.5

    def test_business_cfo_is_c_level(self):
        assert _get_seniority_level("Business CFO") == 8

    def test_deepaka_career_is_ascending(self):
        """Deepaka's career: no downward moves when using numeric seniority."""
        titles = [
            "Account Assistant",   # 2.0
            "Analyst, Finance",    # 2.5
            "Finance Manager",     # 5.0
            "Finance Manager",     # 5.0
            "AVP Finance",         # 6.5
            "Director Finance",    # 6.0
            "Business CFO",        # 8.0
        ]
        levels = [_get_seniority_level(t) for t in titles]
        # No drop should be >= 1.5 (the downward-move threshold)
        for i in range(1, len(levels)):
            drop = levels[i - 1] - levels[i]
            assert drop < 1.5, (
                f"False downward flag: {titles[i-1]} ({levels[i-1]}) → "
                f"{titles[i]} ({levels[i]}), drop = {drop}"
            )
