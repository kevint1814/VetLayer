"""
Candidate Intelligence Brief PDF Generator.

Renders a premium, luxury monochrome PDF using reportlab
based on Design E (centered layout, serif headings, earth tones, thin border).
"""

import io
import logging
from datetime import datetime
from typing import Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle

logger = logging.getLogger(__name__)

# ── Color Palette (luxury monochrome) ────────────────────────────────────
C_BLACK = HexColor("#1a1a1a")
C_DARK = HexColor("#333333")
C_BODY = HexColor("#555555")
C_MUTED = HexColor("#888888")
C_LIGHT = HexColor("#aaaaaa")
C_BORDER = HexColor("#d4cfc5")
C_FAINT = HexColor("#c5c0b8")
C_BG = HexColor("#fafaf8")
C_ACCENT_BG = HexColor("#f5f4f0")
C_CONSIDER_BG = HexColor("#f9f7f3")

# ── Page constants ────────────────────────────────────────────────────────
PAGE_W, PAGE_H = letter  # 612 x 792
MARGIN = 56
INNER_BORDER = 20
CONTENT_W = PAGE_W - 2 * MARGIN
COL_GAP = 30
COL_W = (CONTENT_W - COL_GAP) / 2
LEFT_X = MARGIN
RIGHT_X = MARGIN + COL_W + COL_GAP
FOOTER_Y = 50  # lowest y before footer

# ── Reusable paragraph styles ────────────────────────────────────────────
STYLE_BODY = ParagraphStyle(
    "body", fontName="Helvetica", fontSize=9.5, leading=15,
    textColor=C_BODY, alignment=TA_JUSTIFY,
)
STYLE_BODY_JUSTIFY = ParagraphStyle(
    "bodyJustify", fontName="Helvetica", fontSize=9.5, leading=16,
    textColor=C_BODY, alignment=TA_JUSTIFY,
)
STYLE_BODY_LEFT = ParagraphStyle(
    "bodyLeft", fontName="Helvetica", fontSize=9.5, leading=15,
    textColor=C_BODY, alignment=TA_LEFT,
)
STYLE_STRENGTH = ParagraphStyle(
    "strength", fontName="Helvetica", fontSize=9, leading=14.5,
    textColor=C_DARK, alignment=TA_JUSTIFY, wordWrap="CJK",
)
STYLE_CONSIDER = ParagraphStyle(
    "consider", fontName="Helvetica", fontSize=9, leading=14,
    textColor=C_MUTED, alignment=TA_JUSTIFY, leftIndent=10,
)
STYLE_TP = ParagraphStyle(
    "tp", fontName="Helvetica", fontSize=9.5, leading=15,
    textColor=C_BODY, alignment=TA_JUSTIFY,
)
STYLE_TIMELINE_TITLE = ParagraphStyle(
    "tlTitle", fontName="Helvetica-Bold", fontSize=10, leading=13,
    textColor=C_BLACK, alignment=TA_LEFT,
)
STYLE_TIMELINE_COMP = ParagraphStyle(
    "tlComp", fontName="Helvetica", fontSize=9, leading=12,
    textColor=C_MUTED, alignment=TA_LEFT,
)
STYLE_TIMELINE_DESC = ParagraphStyle(
    "tlDesc", fontName="Helvetica", fontSize=8.5, leading=13,
    textColor=C_BODY, alignment=TA_JUSTIFY,
)
STYLE_SKILL_NARRATIVE = ParagraphStyle(
    "skillNarr", fontName="Helvetica", fontSize=9.5, leading=15,
    textColor=C_BODY, alignment=TA_JUSTIFY,
)
STYLE_CERT_ITEM = ParagraphStyle(
    "certItem", fontName="Helvetica", fontSize=9, leading=14,
    textColor=C_DARK, alignment=TA_LEFT,
)
STYLE_EDU_DETAIL = ParagraphStyle(
    "eduDetail", fontName="Helvetica", fontSize=9, leading=13,
    textColor=C_MUTED, alignment=TA_LEFT,
)


import re as _re

# Known company names with correct casing (LLM often miscapitalizes these)
_COMPANY_CASING = {
    "pwc": "PwC", "kpmg": "KPMG", "ey": "EY", "ibm": "IBM",
    "aws": "AWS", "gcp": "GCP", "wipro": "Wipro", "tcs": "TCS",
    "hcl": "HCL", "jpmorgan": "JPMorgan", "hsbc": "HSBC",
}


def _fix_company_casing(text: str) -> str:
    """Fix known company name casing in text."""
    for wrong, correct in _COMPANY_CASING.items():
        text = _re.sub(rf'\b{_re.escape(wrong)}\b', correct, text, flags=_re.IGNORECASE)
    return text


def _sanitize(text: str) -> str:
    """Remove dashes, emdashes, and endashes from content. Replace with commas or spaces."""
    if not text:
        return text
    text = text.replace(" \u2014 ", ", ")   # spaced emdash → comma
    text = text.replace("\u2014", ", ")      # bare emdash → comma
    text = text.replace(" \u2013 ", ", ")    # spaced endash → comma
    text = text.replace("\u2013", ", ")      # bare endash → comma
    text = text.replace(" -- ", ", ")
    text = text.replace("--", ", ")
    text = text.replace(" - ", ", ")
    # Clean up stray double-spaces from replacements
    while "  " in text:
        text = text.replace("  ", " ")
    # Fix ", ," or " ," artifacts
    text = text.replace(" ,", ",")
    # Fix known company name casing
    text = _fix_company_casing(text)
    return text


def generate_intelligence_brief_pdf(
    candidate: dict,
    parsed: dict,
    profile: Optional[dict] = None,
    ref_code: Optional[str] = None,
) -> bytes:
    """Generate a luxury monochrome Intelligence Brief PDF."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setTitle("Candidate Intelligence Brief")
    c.setAuthor("VetLayer")

    p = profile or {}
    today = datetime.now().strftime("%B %d, %Y")
    import hashlib as _hl
    _name_hash = _hl.sha256(candidate.get("name", "candidate").encode()).hexdigest()[:5].upper()
    ref = ref_code or f"VL-{datetime.now().strftime('%Y')}-{_name_hash}"

    # Track page state for helpers
    state = {"today": today, "ref": ref}

    # ── Page 1 background + border ────────────────────────────────────
    _draw_page_bg(c)
    y = PAGE_H - MARGIN

    # ═══════════════════════════════════════════════════════════════════
    # HEADER: Brand + Date
    # ═══════════════════════════════════════════════════════════════════
    c.setFont("Times-Bold", 18)
    c.setFillColor(C_BLACK)
    c.drawString(MARGIN, y, "VETLAYER")
    c.setFont("Helvetica", 7)
    c.setFillColor(C_FAINT)
    c.drawString(MARGIN, y - 14, "INTELLIGENCE BRIEF")

    c.setFont("Helvetica", 9)
    c.setFillColor(C_MUTED)
    c.drawRightString(PAGE_W - MARGIN, y, today)
    c.setFont("Helvetica", 7)
    c.setFillColor(C_FAINT)
    c.drawRightString(PAGE_W - MARGIN, y - 14, f"REF {ref}")

    y -= 48

    # ═══════════════════════════════════════════════════════════════════
    # CANDIDATE NAME (centered, large serif)
    # ═══════════════════════════════════════════════════════════════════
    name = _sanitize(candidate.get("name", "Candidate"))
    name_upper = name.upper()
    name_size = 30
    while name_size > 16:
        if c.stringWidth(name_upper, "Times-Bold", name_size) <= CONTENT_W:
            break
        name_size -= 1
    c.setFont("Times-Bold", name_size)
    c.setFillColor(C_BLACK)
    c.drawCentredString(PAGE_W / 2, y, name_upper)
    y -= 20

    # Role subtitle line
    role = candidate.get("current_role", "")
    company = candidate.get("current_company", "")
    location = candidate.get("location", "")
    role_parts = [r for r in [role, company, location] if r]
    role_line = _sanitize("  |  ".join(role_parts)).upper() if role_parts else ""
    if role_line:
        rl_size = 9
        while rl_size > 6:
            if c.stringWidth(role_line, "Helvetica", rl_size) <= CONTENT_W:
                break
            rl_size -= 0.5
        c.setFont("Helvetica", rl_size)
        c.setFillColor(C_MUTED)
        c.drawCentredString(PAGE_W / 2, y, role_line)
        y -= 18

    # ── Separator ─────────────────────────────────────────────────────
    _draw_sep(c, y)
    y -= 18

    # ═══════════════════════════════════════════════════════════════════
    # KEY METRICS
    # ═══════════════════════════════════════════════════════════════════
    years_exp = candidate.get("years_experience")
    skills_count = len(parsed.get("skills_mentioned", []))
    education = candidate.get("education_level", "N/A")
    seniority = p.get("seniority_level", "N/A")
    edu_short = _abbreviate_education(education)

    if years_exp is not None and years_exp < 1:
        years_display = "< 1"
    elif years_exp:
        years_display = str(round(years_exp))
    else:
        years_display = "N/A"

    metrics = [
        (years_display, "YEARS EXP"),
        (str(skills_count) if skills_count else "N/A", "SKILLS"),
        (edu_short, "EDUCATION"),
        (seniority, "SENIORITY"),
    ]

    metric_spacing = CONTENT_W / len(metrics)
    max_metric_w = metric_spacing - 16

    for i, (val, label) in enumerate(metrics):
        mx = MARGIN + metric_spacing * i + metric_spacing / 2
        val_str = _sanitize(str(val))
        fs = _fit_font_size(c, val_str, "Times-Roman", 22, 10, max_metric_w)
        c.setFont("Times-Roman", fs)
        c.setFillColor(C_BLACK)
        c.drawCentredString(mx, y, val_str)
        c.setFont("Helvetica", 6)
        c.setFillColor(C_FAINT)
        c.drawCentredString(mx, y - 14, label)

    y -= 36

    # ═══════════════════════════════════════════════════════════════════
    # EXECUTIVE SUMMARY (justified)
    # ═══════════════════════════════════════════════════════════════════
    summary_text = _sanitize(p.get("executive_summary") or parsed.get("summary") or "")
    if summary_text:
        para = Paragraph(summary_text, STYLE_BODY_JUSTIFY)
        pw = CONTENT_W - 20
        _, h = para.wrap(pw, 300)
        para.drawOn(c, MARGIN + 10, y - h)
        y -= h + 8

    _draw_sep(c, y)
    y -= 14

    # ═══════════════════════════════════════════════════════════════════
    # CAREER NARRATIVE + KEY STRENGTHS (two columns)
    # ═══════════════════════════════════════════════════════════════════
    y_left = y
    y_right = y

    # Left: Career Narrative
    y_left = _section_label(c, "CAREER NARRATIVE", LEFT_X, COL_W, y_left)
    narrative = _sanitize(p.get("career_narrative", ""))
    if not narrative:
        narrative = f"Professional with background in {_sanitize(candidate.get('current_role', 'their field'))}."
    y_left = _para(c, narrative, STYLE_BODY, LEFT_X, COL_W, y_left)

    # Right: Key Strengths
    y_right = _section_label(c, "KEY STRENGTHS", RIGHT_X, COL_W, y_right)
    for s in p.get("strengths", [])[:4]:
        y_right = _para(c, _sanitize(s), STYLE_STRENGTH, RIGHT_X, COL_W, y_right)
        c.setStrokeColor(HexColor("#eae6df"))
        c.setLineWidth(0.4)
        c.line(RIGHT_X, y_right + 3, RIGHT_X + COL_W, y_right + 3)

    y = min(y_left, y_right) - 4
    _draw_sep(c, y)
    y -= 14

    # ═══════════════════════════════════════════════════════════════════
    # SKILLS NARRATIVE + CAREER TIMELINE (two columns)
    # ═══════════════════════════════════════════════════════════════════
    experience = parsed.get("experience", [])  # Show all roles — pagination handles overflow
    skill_narrative = _sanitize(p.get("skill_narrative", ""))
    skill_categories = p.get("skill_categories", {})
    raw_skills = parsed.get("skills_mentioned", [])
    skills_prose = _build_skills_prose(skill_narrative, skill_categories, raw_skills)

    # Build AI brief lookup by company+title to prevent cross-role copy-paste
    timeline_briefs = {}
    # Track multiple briefs per company for positional fallback
    _company_briefs_list: dict[str, list[str]] = {}
    for tb in p.get("career_timeline_briefs", []):
        if isinstance(tb, dict):
            company = tb.get("company", "").lower().strip()
            title = tb.get("title", "").lower().strip()
            brief = tb.get("brief", "")
            if not company or not brief:
                continue
            # Key by company+title for exact matching
            if title:
                timeline_briefs[f"{company}|{title}"] = brief
            # Collect all briefs per company in order (for positional matching)
            _company_briefs_list.setdefault(company, []).append(brief)
            # Company-only key: only store if there's exactly one role at this company
            # (avoids first-role-wins when multiple roles exist at same company)
    for comp, briefs_list in _company_briefs_list.items():
        if len(briefs_list) == 1:
            timeline_briefs[comp] = briefs_list[0]
    # Store the positional list for multi-role companies
    timeline_briefs["__positional__"] = _company_briefs_list

    # Check if we have enough space on current page (need ~200px minimum)
    if y < 200:
        _draw_footer(c)
        y = _new_page(c, state)

    y_left = y
    y_right = y

    # Left: Skills Profile (prose paragraph)
    y_left = _section_label(c, "SKILLS PROFILE", LEFT_X, COL_W, y_left)
    if skills_prose:
        # Pre-measure to ensure it doesn't overflow past footer
        test_para = Paragraph(skills_prose, STYLE_SKILL_NARRATIVE)
        _, test_h = test_para.wrap(COL_W, 400)
        max_avail = y_left - FOOTER_Y - 10
        if test_h > max_avail and max_avail > 60:
            ratio = max_avail / test_h
            truncated = skills_prose[:int(len(skills_prose) * ratio * 0.85)]
            last_period = truncated.rfind(".")
            if last_period > len(truncated) // 2:
                skills_prose = truncated[:last_period + 1]
        y_left = _para(c, skills_prose, STYLE_SKILL_NARRATIVE, LEFT_X, COL_W, y_left)

    # Right: Career Timeline (with AI-generated descriptions)
    # Pre-compute per-company role index for positional brief matching
    _company_role_counters: dict[str, int] = {}
    _exp_role_index: list[int] = []
    for exp in experience:
        comp_key = (exp.get("company") or "").lower().strip()
        idx_at_comp = _company_role_counters.get(comp_key, 0)
        _exp_role_index.append(idx_at_comp)
        _company_role_counters[comp_key] = idx_at_comp + 1

    # Render entries that fit; collect remaining for overflow continuation
    y_right = _section_label(c, "CAREER TIMELINE", RIGHT_X, COL_W, y_right)
    remaining_exp = []
    remaining_indices = []
    if not experience:
        # No work experience — show N/A
        c.setFont("Helvetica", 9.5)
        c.setFillColor(C_MUTED)
        c.drawString(RIGHT_X, y_right, "No professional experience on record.")
        y_right -= 16
    else:
        for idx, exp in enumerate(experience):
            entry_est = _estimate_timeline_entry_h(c, exp)
            if y_right - entry_est < FOOTER_Y + 10:
                remaining_exp = experience[idx:]
                remaining_indices = _exp_role_index[idx:]
                break
            y_right = _draw_timeline_entry(c, exp, timeline_briefs, RIGHT_X, COL_W, y_right, _exp_role_index[idx])

    y = min(y_left, y_right) - 4

    # Guard: if content got very close to footer, break before separator
    if y < FOOTER_Y + 20:
        _draw_footer(c)
        y = _new_page(c, state)

    # If there are remaining timeline entries, continue on current or new page
    if remaining_exp:
        # Check if there's room for at least the label + one entry (~100px)
        min_needed = 100
        if y - min_needed < FOOTER_Y + 10:
            _draw_footer(c)
            y = _new_page(c, state)

        _draw_sep(c, y)
        y -= 14
        y = _section_label_centered(c, "CAREER TIMELINE (CONTINUED)", y)
        for ri, exp in enumerate(remaining_exp):
            entry_est = _estimate_timeline_entry_h(c, exp)
            if y - entry_est < FOOTER_Y + 10:
                _draw_footer(c)
                y = _new_page(c, state)
            r_idx = remaining_indices[ri] if ri < len(remaining_indices) else 0
            y = _draw_timeline_entry(c, exp, timeline_briefs, MARGIN, CONTENT_W, y, r_idx)

    _draw_sep(c, y)
    y -= 14

    # ═══════════════════════════════════════════════════════════════════
    # IDEAL ROLES + CULTURE SIGNALS (two columns)
    # ═══════════════════════════════════════════════════════════════════
    ideal_roles_narrative = _sanitize(p.get("ideal_roles_narrative", ""))
    ideal_roles_list = p.get("ideal_roles", [])
    culture = _sanitize(p.get("culture_signals", ""))
    considerations = p.get("considerations", [])

    # Ideal Roles (prose paragraph) + Culture Signals (two columns)
    has_roles = ideal_roles_narrative or ideal_roles_list
    if has_roles or culture:
        # Pre-measure actual height needed for both columns
        roles_text = ""
        if has_roles:
            roles_text = ideal_roles_narrative
            if not roles_text and ideal_roles_list:
                roles_text = "Based on this profile, ideal fits include " + ", ".join(
                    _sanitize(r) for r in ideal_roles_list[:4]
                ) + "."

        est_left = 14  # section label
        if roles_text:
            test_p = Paragraph(roles_text, STYLE_BODY)
            _, th = test_p.wrap(COL_W, 400)
            est_left += th + 5

        est_right = 14  # section label
        if culture:
            test_p = Paragraph(culture, STYLE_BODY)
            _, th = test_p.wrap(COL_W, 400)
            est_right += th + 5

        est_h = max(est_left, est_right) + 10
        if y - est_h < FOOTER_Y:
            _draw_footer(c)
            y = _new_page(c, state)

        y_left = y
        y_right = y

        if has_roles:
            y_left = _section_label(c, "IDEAL ROLES", LEFT_X, COL_W, y_left)
            if roles_text:
                y_left = _para(c, roles_text, STYLE_BODY, LEFT_X, COL_W, y_left)

        if culture:
            y_right = _section_label(c, "CULTURE SIGNALS", RIGHT_X, COL_W, y_right)
            y_right = _para(c, culture, STYLE_BODY, RIGHT_X, COL_W, y_right)

        y = min(y_left, y_right) - 4

    # Guard: page break if near footer
    if y < FOOTER_Y + 30:
        _draw_footer(c)
        y = _new_page(c, state)

    _draw_sep(c, y)
    y -= 14

    # ═══════════════════════════════════════════════════════════════════
    # CONSIDERATIONS
    # ═══════════════════════════════════════════════════════════════════
    if considerations:
        est_h = len(considerations) * 50 + 20
        if y - est_h < FOOTER_Y:
            _draw_footer(c)
            y = _new_page(c, state)

        y = _section_label_centered(c, "CONSIDERATIONS", y)
        for con in considerations[:3]:
            con_text = _sanitize(str(con) if not isinstance(con, str) else con)
            para = Paragraph(con_text, STYLE_CONSIDER)
            _, h = para.wrap(CONTENT_W - 20, 200)
            c.setStrokeColor(C_BORDER)
            c.setLineWidth(1.5)
            c.line(MARGIN, y - h + 2, MARGIN, y + 2)
            c.setFillColor(C_CONSIDER_BG)
            c.rect(MARGIN + 2, y - h, CONTENT_W - 2, h + 4, fill=1, stroke=0)
            para.drawOn(c, MARGIN + 12, y - h + 2)
            y -= h + 8

    # ═══════════════════════════════════════════════════════════════════
    # RECRUITER TALKING POINTS
    # ═══════════════════════════════════════════════════════════════════
    talking_points = p.get("talking_points", [])
    if talking_points:
        tp_est = len(talking_points) * 22 + 20
        if y - tp_est < FOOTER_Y:
            _draw_footer(c)
            y = _new_page(c, state)

        _draw_sep(c, y)
        y -= 14
        y = _section_label_centered(c, "RECRUITER TALKING POINTS", y)

        for i, tp in enumerate(talking_points[:5], 1):
            if y < FOOTER_Y + 20:
                _draw_footer(c)
                y = _new_page(c, state)

            tp_text = _sanitize(tp)
            c.setFont("Times-Roman", 16)
            c.setFillColor(C_FAINT)
            c.drawString(MARGIN, y, str(i))

            para = Paragraph(tp_text, STYLE_TP)
            pw = CONTENT_W - 30
            _, h = para.wrap(pw, 200)
            para.drawOn(c, MARGIN + 24, y - h + 10)
            y -= max(h, 14) + 4

    # ═══════════════════════════════════════════════════════════════════
    # EDUCATION & CREDENTIALS
    # ═══════════════════════════════════════════════════════════════════
    education_list = _dedupe_education(parsed.get("education", []))
    certs = parsed.get("certifications", [])
    if education_list or certs:
        # Need at least ~45px for section header + one education entry
        if y < FOOTER_Y + 45:
            _draw_footer(c)
            y = _new_page(c, state)

        _draw_sep(c, y)
        y -= 14
        y = _section_label_centered(c, "EDUCATION & CREDENTIALS", y)

        for edu in education_list[:4]:
            # Per-item overflow check (~32px per edu entry)
            if y < FOOTER_Y + 32:
                _draw_footer(c)
                y = _new_page(c, state)

            degree = _sanitize(edu.get("degree", ""))
            field_of_study = _sanitize(edu.get("field", ""))
            institution = _sanitize(edu.get("institution", ""))
            grad_date = _sanitize(edu.get("graduation_date", ""))
            gpa = edu.get("gpa")

            deg_line = f"{degree} in {field_of_study}" if degree and field_of_study else degree or field_of_study or "Degree"
            c.setFont("Helvetica-Bold", 10)
            c.setFillColor(C_BLACK)
            c.drawString(MARGIN, y, deg_line)
            y -= 12  # tight spacing to institution line

            detail_parts = []
            if institution:
                detail_parts.append(institution)
            if grad_date:
                detail_parts.append(grad_date)
            if gpa:
                gpa_str = str(gpa)
                try:
                    gpa_val = float(gpa_str.replace("/10", "").replace("/4", "").strip())
                    if gpa_val > 4.0:
                        detail_parts.append(f"CGPA: {gpa_str}")
                    else:
                        detail_parts.append(f"GPA: {gpa_str}")
                except (ValueError, TypeError):
                    detail_parts.append(f"GPA: {gpa_str}")

            if detail_parts:
                detail = "  |  ".join(detail_parts)
                c.setFont("Helvetica", 9)
                c.setFillColor(C_MUTED)
                c.drawString(MARGIN, y, detail)
                y -= 18  # gap before next degree

        # Certifications
        if certs:
            if y < FOOTER_Y + 30:
                _draw_footer(c)
                y = _new_page(c, state)

            y -= 4
            c.setFont("Helvetica-Bold", 6.5)
            c.setFillColor(C_BLACK)
            c.drawCentredString(PAGE_W / 2, y, "CERTIFICATIONS")
            y -= 14

            for ct in certs[:8]:
                if y < FOOTER_Y + 16:
                    _draw_footer(c)
                    y = _new_page(c, state)

                cert_name = _sanitize(ct if isinstance(ct, str) else ct.get("name", "Cert"))
                cert_issuer = "" if isinstance(ct, str) else _sanitize(ct.get("issuer", ""))
                cert_date = "" if isinstance(ct, str) else _sanitize(ct.get("date", ""))

                c.setFillColor(C_BORDER)
                c.circle(MARGIN + 4, y + 3, 2, fill=1, stroke=0)

                cert_display = cert_name
                if cert_issuer:
                    cert_display += f"  ({cert_issuer})"
                if cert_date:
                    cert_display += f"  {cert_date}"

                para = Paragraph(cert_display, STYLE_CERT_ITEM)
                _, h = para.wrap(CONTENT_W - 20, 40)
                para.drawOn(c, MARGIN + 14, y - h + 12)
                y -= max(h, 14) + 2

    # ═══════════════════════════════════════════════════════════════════
    # FOOTER (on last page)
    # ═══════════════════════════════════════════════════════════════════
    _draw_footer(c)
    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()

    logger.info(f"Generated Intelligence Brief PDF for {candidate.get('name', 'unknown')} ({len(pdf_bytes)} bytes)")
    return pdf_bytes


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def _draw_page_bg(c):
    """Draw page background and inner border frame."""
    c.setFillColor(C_BG)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setStrokeColor(C_BORDER)
    c.setLineWidth(0.5)
    c.rect(INNER_BORDER, INNER_BORDER,
           PAGE_W - 2 * INNER_BORDER, PAGE_H - 2 * INNER_BORDER,
           fill=0, stroke=1)


def _draw_sep(c, y):
    """Draw a subtle centered separator line."""
    c.setStrokeColor(C_BORDER)
    c.setLineWidth(0.3)
    c.line(MARGIN + 80, y, PAGE_W - MARGIN - 80, y)


def _draw_footer(c):
    """Draw confidential footer text."""
    c.setFont("Helvetica", 6)
    c.setFillColor(C_FAINT)
    c.drawCentredString(
        PAGE_W / 2, 32,
        "CONFIDENTIAL  |  FOR INTERNAL RECRUITER USE ONLY  |  VETLAYER INTELLIGENCE BRIEF"
    )


def _new_page(c, state):
    """Start a new page. Returns starting y."""
    c.showPage()
    _draw_page_bg(c)
    y = PAGE_H - MARGIN
    c.setFont("Times-Bold", 12)
    c.setFillColor(C_BLACK)
    c.drawString(MARGIN, y, "VETLAYER")
    c.setFont("Helvetica", 7)
    c.setFillColor(C_FAINT)
    c.drawRightString(PAGE_W - MARGIN, y, state["today"])
    _draw_footer(c)
    return y - 30


def _section_label(c, text, x, width, y):
    """Draw small uppercase section label centered within a column."""
    c.setFont("Helvetica-Bold", 6.5)
    c.setFillColor(C_BLACK)
    c.drawCentredString(x + width / 2, y, text)
    return y - 14


def _section_label_centered(c, text, y):
    """Draw full width centered section label."""
    c.setFont("Helvetica-Bold", 6.5)
    c.setFillColor(C_BLACK)
    c.drawCentredString(PAGE_W / 2, y, text)
    return y - 14


def _para(c, text, style, x, width, y):
    """Draw a paragraph. Returns new y."""
    para = Paragraph(text, style)
    _, h = para.wrap(width, 400)
    para.drawOn(c, x, y - h)
    return y - h - 5


def _fit_font_size(c, text, font, max_size, min_size, max_w):
    """Find the largest font size that fits text within max_w."""
    fs = max_size
    while fs > min_size:
        if c.stringWidth(text, font, fs) <= max_w:
            return fs
        fs -= 1
    return min_size


def _draw_pills_centered(c, items, y, font_size=9, pill_h=20, pad=24):
    """Draw pills centered, wrapping to multiple lines if needed."""
    pill_widths = []
    for text in items:
        tw = c.stringWidth(text, "Helvetica", font_size) + pad
        pill_widths.append((text, tw))

    lines = []
    current_line = []
    current_w = 0
    gap = 8
    for text, tw in pill_widths:
        needed = tw + (gap if current_line else 0)
        if current_w + needed > CONTENT_W and current_line:
            lines.append(current_line)
            current_line = []
            current_w = 0
        current_line.append((text, tw))
        current_w += tw + (gap if len(current_line) > 1 else 0)
    if current_line:
        lines.append(current_line)

    for line in lines:
        total_w = sum(tw for _, tw in line) + gap * (len(line) - 1)
        rx = (PAGE_W - total_w) / 2

        for text, tw in line:
            c.setStrokeColor(C_BORDER)
            c.setLineWidth(0.4)
            c.setFillColor(C_BG)
            c.roundRect(rx, y - 4, tw, pill_h, 1, fill=1, stroke=1)
            c.setFont("Helvetica", font_size)
            c.setFillColor(C_DARK)
            c.drawCentredString(rx + tw / 2, y, text)
            rx += tw + gap

        y -= pill_h + 6

    return y


def _abbreviate_education(edu: str) -> str:
    """Shorten education text for metric display.

    Order matters: longer/more-specific patterns must come first to prevent
    partial matches (e.g., 'Bachelor of Business Administration' before 'Bachelor's').
    """
    if not edu or edu == "N/A":
        return edu

    # Check full-string matches first (case-insensitive)
    edu_lower = edu.lower().strip()
    full_matches = {
        "bachelor's": "Bachelor's",
        "master's": "Master's",
        "diploma": "Diploma",
        "high school": "High School",
        "phd": "Ph.D.",
        "doctorate": "Ph.D.",
        "professional": "Professional",
    }
    if edu_lower in full_matches:
        return full_matches[edu_lower]

    # Ordered replacements — longest/most-specific first
    replacements = [
        # Full degree names (must come before partial matches)
        ("Bachelor of Business Administration", "BBA"),
        ("Bachelor of Commerce", "B.Com"),
        ("Bachelor of Engineering", "B.E."),
        ("Bachelor of Technology", "B.Tech"),
        ("Bachelor of Science", "B.Sc."),
        ("Bachelor of Arts", "B.A."),
        ("Master of Business Administration", "MBA"),
        ("Master of Science", "M.Sc."),
        ("Master of Arts", "M.A."),
        ("Master of Technology", "M.Tech"),
        ("Master of Engineering", "M.E."),
        ("Master of Commerce", "M.Com"),
        ("Doctor of Philosophy", "Ph.D."),
        # Field abbreviations
        ("Computer Science", "CS"),
        ("computer science", "CS"),
        ("Information Technology", "IT"),
        ("information technology", "IT"),
        ("Business Administration", "BA"),
        ("Electrical Engineering", "EE"),
        ("Mechanical Engineering", "ME"),
        ("Electronics & Communication Engineering", "ECE"),
        ("Electronics and Communication Engineering", "ECE"),
        ("Software Engineering", "SWE"),
        ("Data Science", "Data Sci"),
    ]
    result = edu
    for long, short in replacements:
        result = result.replace(long, short)

    if len(result) > 16:
        result = result[:15] + "."
    return result


def _dedupe_education(edu_list: list) -> list:
    """Remove duplicate education entries (same degree or same institution+degree combo)."""
    if not edu_list:
        return edu_list
    seen = set()
    deduped = []
    for edu in edu_list:
        if not isinstance(edu, dict):
            continue
        degree = (edu.get("degree") or "").strip().lower()
        institution = (edu.get("institution") or "").strip().lower()
        field = (edu.get("field") or "").strip().lower()
        # Create a key from degree+institution, or just degree if no institution
        key = f"{degree}|{institution}" if institution else degree
        if key and key in seen:
            continue
        # Also catch near-duplicates: same degree type from different formatting
        # e.g. "class x" and "class 10" or "sslc"
        degree_norm = degree.replace("class ", "").replace("10th", "x").replace("10", "x").replace("12th", "xii").replace("12", "xii").replace("sslc", "x").replace("hsc", "xii")
        alt_key = f"{degree_norm}|norm"
        if degree_norm and alt_key in seen:
            continue
        seen.add(key)
        if degree_norm:
            seen.add(alt_key)
        deduped.append(edu)
    return deduped


def _build_skills_prose(skill_narrative: str, skill_categories: dict, raw_skills: list) -> str:
    """Build a concise prose paragraph describing the candidate's skills.
    Uses skill_narrative from AI profile only. Falls back to a brief
    natural language summary if no narrative is available."""

    if skill_narrative:
        return skill_narrative

    # Fallback: build a brief summary from raw data
    if skill_categories:
        top_cats = list(skill_categories.items())[:4]
        phrases = []
        for cat_name, skills in top_cats:
            if skills:
                cat_clean = _sanitize(cat_name).lower()
                top_skills = ", ".join(_sanitize(s) for s in skills[:4])
                phrases.append(f"{cat_clean} including {top_skills}")
        if phrases:
            return "Technical competencies span " + ", as well as ".join(phrases) + "."

    if raw_skills:
        return "Key skills include " + ", ".join(_sanitize(s) for s in raw_skills[:10]) + "."

    return ""


def _draw_timeline_entry(c, exp, timeline_briefs, x, width, y, role_index_at_company: int = 0):
    """Draw a single career timeline entry. Returns new y."""
    title = _sanitize(exp.get("title", "Role"))
    comp = _sanitize(exp.get("company", ""))
    start = _sanitize(exp.get("start_date", ""))
    end = _sanitize(exp.get("end_date") or "Present")
    dates = f"{start}  to  {end}" if start else ""

    # Look up AI-generated brief (no resume fallback — brief is AI only)
    ai_brief = _find_timeline_brief(timeline_briefs, comp, title, role_index_at_company)

    if dates:
        c.setFont("Helvetica", 8)
        c.setFillColor(C_LIGHT)
        c.drawString(x, y, dates)
        y -= 13

    para = Paragraph(title, STYLE_TIMELINE_TITLE)
    _, h = para.wrap(width, 80)
    para.drawOn(c, x, y - h)
    y -= h + 2

    if comp:
        para = Paragraph(comp, STYLE_TIMELINE_COMP)
        _, h = para.wrap(width, 30)
        para.drawOn(c, x, y - h)
        y -= h + 4

    if ai_brief:
        para = Paragraph(_sanitize(ai_brief), STYLE_TIMELINE_DESC)
        _, h = para.wrap(width, 200)
        para.drawOn(c, x, y - h)
        y -= h + 3

    c.setStrokeColor(HexColor("#eae6df"))
    c.setLineWidth(0.3)
    c.line(x, y, x + width, y)
    y -= 8
    return y


def _find_timeline_brief(briefs: dict, company: str, title: str = "", role_index_at_company: int = 0) -> str:
    """Look up AI-generated brief for a company+title combo.

    Matching priority:
    1. Exact company+title compound key
    2. Fuzzy company+title (title words overlap)
    3. Exact company-only key (only if single role at company)
    4. Positional match within same company (for multi-role companies without title keys)
    5. First-word fuzzy on company name
    Does NOT do substring matching to prevent cross-company copy-paste.
    """
    if not briefs or not company:
        return ""
    comp_lower = company.lower().strip()
    title_lower = title.lower().strip() if title else ""

    # Priority 1: Exact company+title match
    if title_lower:
        compound_key = f"{comp_lower}|{title_lower}"
        if compound_key in briefs:
            return briefs[compound_key]

    # Priority 2: Fuzzy title match — find compound keys for this company and pick best title overlap
    if title_lower:
        title_words = set(title_lower.split())
        best_match = ""
        best_overlap = 0
        for key, val in briefs.items():
            if "|" not in key:
                continue
            key_comp, key_title = key.split("|", 1)
            if key_comp != comp_lower:
                # Also check first-word match for company
                if not (key_comp.split()[0] == comp_lower.split()[0] if comp_lower.split() and key_comp.split() else False):
                    continue
            key_title_words = set(key_title.split())
            overlap = len(title_words & key_title_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = val
        if best_match and best_overlap >= 1:
            return best_match

    # Priority 3: Exact company match (only stored if single role at company)
    if comp_lower in briefs and not isinstance(briefs[comp_lower], (dict, list)):
        return briefs[comp_lower]

    # Priority 4: Positional match for multi-role companies
    positional = briefs.get("__positional__", {})
    comp_briefs = positional.get(comp_lower, [])
    if not comp_briefs:
        # Try first-word match on company
        comp_first = comp_lower.split()[0] if comp_lower else ""
        if comp_first and len(comp_first) >= 3:
            for pkey, pval in positional.items():
                pfirst = pkey.split()[0] if pkey else ""
                if pfirst == comp_first:
                    comp_briefs = pval
                    break
    if comp_briefs and role_index_at_company < len(comp_briefs):
        return comp_briefs[role_index_at_company]

    # Priority 5: First-word match only (conservative fuzzy)
    comp_first = comp_lower.split()[0] if comp_lower else ""
    if comp_first and len(comp_first) >= 3:
        for key, val in briefs.items():
            if "|" in key or key == "__positional__":
                continue
            key_first = key.split()[0] if key else ""
            if key_first and comp_first == key_first and isinstance(val, str):
                return val
    return ""


def _estimate_timeline_entry_h(c, exp: dict) -> int:
    """Rough height estimate for a single timeline entry."""
    h = 13  # dates line
    title = exp.get("title", "Role")
    tw = c.stringWidth(title, "Helvetica-Bold", 10)
    title_lines = max(1, int(tw / COL_W) + 1)
    h += title_lines * 13 + 2  # title
    h += 16  # company
    desc = exp.get("description", "")
    if desc:
        short = _truncate_to_sentences(desc, 2, 180)
        h += max(26, len(short) // 3) + 3  # description estimate
    h += 8  # separator gap
    return h


def _truncate_to_sentences(text: str, max_sentences: int = 2, max_chars: int = 180) -> str:
    """Truncate text to N sentences or max characters, whichever is shorter."""
    if not text:
        return ""

    sentences = []
    current = ""
    for ch in text:
        current += ch
        if ch in ".!?" and len(current.strip()) > 5:
            sentences.append(current.strip())
            current = ""
            if len(sentences) >= max_sentences:
                break
    if current.strip() and len(sentences) < max_sentences:
        sentences.append(current.strip())

    result = " ".join(sentences)
    if len(result) > max_chars:
        result = result[:max_chars - 3].rstrip() + "..."
    return result
