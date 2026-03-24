"""
Unit tests for date parsing utilities and core analysis logic.

Tests the shared date functions used across risk flag generation,
gap detection, overlap detection, and total experience calculation.
"""

import pytest
import sys
import os

# Add parent to path so we can import from app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.routes.analysis import (
    _extract_month,
    _extract_year,
    _is_present_date,
    _date_to_months,
    _parse_experience_dates,
    _sort_experiences_by_start,
    _estimate_gap_months,
)


# ═══════════════════════════════════════════════════════════════════════
# _extract_month
# ═══════════════════════════════════════════════════════════════════════

class TestExtractMonth:
    def test_full_month_name(self):
        assert _extract_month("January 2020") == 1
        assert _extract_month("December 2020") == 12
        assert _extract_month("March 2021") == 3

    def test_abbreviated_month(self):
        assert _extract_month("Jan 2020") == 1
        assert _extract_month("Dec 2020") == 12
        assert _extract_month("Sep 2019") == 9

    def test_numeric_date(self):
        assert _extract_month("01/2020") == 1
        assert _extract_month("12/2020") == 12

    def test_yyyy_mm_iso_format(self):
        """YYYY-MM is the most common format from VetLayer's resume parser."""
        assert _extract_month("2022-09") == 9
        assert _extract_month("2004-11") == 11
        assert _extract_month("2008-01") == 1
        assert _extract_month("2014-02") == 2

    def test_yyyy_mm_slash_format(self):
        assert _extract_month("2022/09") == 9

    def test_mm_dash_yyyy_still_works(self):
        """Ensure MM-YYYY still works after adding YYYY-MM support."""
        assert _extract_month("09-2022") == 9
        assert _extract_month("11-2004") == 11

    def test_no_month(self):
        assert _extract_month("2020") == 6  # Default to mid-year
        assert _extract_month("") == 6

    def test_case_insensitive(self):
        assert _extract_month("JANUARY 2020") == 1
        assert _extract_month("january 2020") == 1
        assert _extract_month("JaN 2020") == 1


# ═══════════════════════════════════════════════════════════════════════
# _extract_year
# ═══════════════════════════════════════════════════════════════════════

class TestExtractYear:
    def test_four_digit_year(self):
        assert _extract_year("January 2020") == 2020
        assert _extract_year("2023") == 2023
        assert _extract_year("March 2015") == 2015

    def test_no_year(self):
        assert _extract_year("Present") is None
        assert _extract_year("Current") is None
        assert _extract_year("") is None

    def test_year_in_complex_string(self):
        assert _extract_year("Jan 2020 - Dec 2023") == 2020  # First 4-digit year


# ═══════════════════════════════════════════════════════════════════════
# _is_present_date
# ═══════════════════════════════════════════════════════════════════════

class TestIsPresentDate:
    def test_present_variants(self):
        assert _is_present_date("Present") is True
        assert _is_present_date("present") is True
        assert _is_present_date("PRESENT") is True
        assert _is_present_date("Current") is True
        assert _is_present_date("current") is True
        # Note: "Now" is NOT recognized — only "present" and "current" keywords

    def test_non_present(self):
        assert _is_present_date("December 2023") is False
        assert _is_present_date("2020") is False
        assert _is_present_date("Jan 2019") is False

    def test_empty(self):
        assert _is_present_date("") is False
        assert _is_present_date(None) is False


# ═══════════════════════════════════════════════════════════════════════
# _date_to_months
# ═══════════════════════════════════════════════════════════════════════

class TestDateToMonths:
    def test_normal_date(self):
        result = _date_to_months("January 2020")
        assert result == 2020 * 12 + 1

    def test_present_date(self):
        from datetime import datetime
        now = datetime.now()
        result = _date_to_months("Present")
        assert result == now.year * 12 + now.month

    def test_year_only(self):
        result = _date_to_months("2020")
        assert result == 2020 * 12 + 6  # Defaults to mid-year (June)

    def test_no_valid_date(self):
        result = _date_to_months("")
        assert result is None

    def test_with_fallback_year(self):
        result = _date_to_months("", fallback_year=2020)
        # Empty string with fallback year uses mid-year default
        assert result == 2020 * 12 + 6


# ═══════════════════════════════════════════════════════════════════════
# _parse_experience_dates
# ═══════════════════════════════════════════════════════════════════════

class TestParseExperienceDates:
    def test_normal_range(self):
        exp = {"start_date": "January 2020", "end_date": "December 2023"}
        start, end = _parse_experience_dates(exp)
        assert start == 2020 * 12 + 1
        assert end == 2023 * 12 + 12

    def test_present_end(self):
        exp = {"start_date": "March 2022", "end_date": "Present"}
        start, end = _parse_experience_dates(exp)
        assert start == 2022 * 12 + 3
        assert end is not None
        assert end >= start  # Present should be >= start

    def test_missing_dates(self):
        exp = {}
        start, end = _parse_experience_dates(exp)
        assert start is None
        assert end is None


# ═══════════════════════════════════════════════════════════════════════
# _sort_experiences_by_start
# ═══════════════════════════════════════════════════════════════════════

class TestSortExperiences:
    def test_sort_order(self):
        exps = [
            {"start_date": "March 2022", "end_date": "Present"},
            {"start_date": "January 2018", "end_date": "February 2020"},
            {"start_date": "June 2020", "end_date": "January 2022"},
        ]
        sorted_exps = _sort_experiences_by_start(exps)
        # Should be sorted by start date ascending (earliest first)
        assert "2018" in sorted_exps[0]["start_date"]
        assert "2020" in sorted_exps[1]["start_date"]
        assert "2022" in sorted_exps[2]["start_date"]

    def test_empty_list(self):
        assert _sort_experiences_by_start([]) == []


# ═══════════════════════════════════════════════════════════════════════
# _estimate_gap_months
# ═══════════════════════════════════════════════════════════════════════

class TestEstimateGapMonths:
    def test_normal_gap(self):
        gap = _estimate_gap_months("December 2020", "March 2021")
        assert gap is not None
        assert gap == 3  # Dec 2020 to Mar 2021 = 3 months

    def test_no_gap(self):
        gap = _estimate_gap_months("December 2020", "January 2021")
        assert gap is not None
        assert gap == 1

    def test_overlap(self):
        gap = _estimate_gap_months("March 2021", "January 2021")
        # End of first job is after start of second — negative or zero gap
        assert gap is not None
        assert gap <= 0

    def test_yyyy_mm_gap_detection(self):
        """YYYY-MM dates must produce correct gaps, not 48-month phantoms."""
        # ACS (ended 2004-11) → MphasiS (started 2004-11): gap = 0
        gap = _estimate_gap_months("2004-11", "2004-11")
        assert gap is not None
        assert gap == 0

    def test_yyyy_mm_continuous_career(self):
        """End-to-end: Deepaka's career through analysis.py date functions."""
        career = [
            {"start_date": "2003-06", "end_date": "2004-03"},
            {"start_date": "2004-06", "end_date": "2004-11"},
            {"start_date": "2004-11", "end_date": "2008-01"},
            {"start_date": "2008-01", "end_date": "2009-01"},
            {"start_date": "2009-02", "end_date": "2014-01"},
            {"start_date": "2014-02", "end_date": "2022-08"},
            {"start_date": "2022-09", "end_date": "2025-09"},
        ]
        sorted_career = _sort_experiences_by_start(career)
        # No gap should exceed 6 months (the non-senior threshold)
        for i in range(len(sorted_career) - 1):
            end = sorted_career[i].get("end_date", "")
            start = sorted_career[i + 1].get("start_date", "")
            gap = _estimate_gap_months(end, start)
            assert gap is not None, f"Gap between {end} and {start} returned None"
            assert gap <= 6, f"Phantom gap of {gap} months between {end} and {start}"


class TestDateToMonthsYYYYMM:
    """Regression: _date_to_months in analysis.py must handle YYYY-MM."""

    def test_yyyy_mm_date(self):
        assert _date_to_months("2022-09") == 2022 * 12 + 9

    def test_yyyy_mm_preserves_month(self):
        assert _date_to_months("2004-11") == 2004 * 12 + 11
        assert _date_to_months("2008-01") == 2008 * 12 + 1
