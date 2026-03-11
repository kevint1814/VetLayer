"""
Batch Analysis Brief PDF Generator.

Renders a multi-page luxury monochrome PDF using reportlab:
  - Page 1: Batch Overview (metrics, rankings, pool strengths & gaps)
  - Pages 2..N: Individual candidate deep dive (one per candidate)
  - Final Page: Comparative Analysis (skill matrix, considerations, recommendations)

Design matches the Candidate Intelligence Brief (Design E).
"""

import io
import logging
from datetime import datetime
from typing import List, Dict, Optional, Any

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

C_GREEN = HexColor("#3d7a4a")
C_GREEN_BG = HexColor("#f0f6f1")
C_RED = HexColor("#9a3c3c")
C_RED_BG = HexColor("#faf4f4")
C_AMBER = HexColor("#8a6d2b")
C_AMBER_BG = HexColor("#faf6ee")

# ── Page constants ────────────────────────────────────────────────────────
PAGE_W, PAGE_H = letter  # 612 x 792
MARGIN = 56
INNER_BORDER = 20
CONTENT_W = PAGE_W - 2 * MARGIN
COL_GAP = 30
COL_W = (CONTENT_W - COL_GAP) / 2
LEFT_X = MARGIN
RIGHT_X = MARGIN + COL_W + COL_GAP
FOOTER_Y = 50

# ── Reusable paragraph styles ────────────────────────────────────────────
STYLE_BODY = ParagraphStyle(
    "body", fontName="Helvetica", fontSize=9.5, leading=15,
    textColor=C_BODY, alignment=TA_JUSTIFY,
)
STYLE_BODY_LEFT = ParagraphStyle(
    "bodyLeft", fontName="Helvetica", fontSize=9.5, leading=15,
    textColor=C_BODY, alignment=TA_LEFT,
)
STYLE_STRENGTH = ParagraphStyle(
    "strength", fontName="Helvetica", fontSize=9, leading=14.5,
    textColor=C_DARK, alignment=TA_JUSTIFY,
)
STYLE_GAP = ParagraphStyle(
    "gap", fontName="Helvetica", fontSize=9, leading=14.5,
    textColor=C_MUTED, alignment=TA_JUSTIFY,
)
STYLE_CONSIDER = ParagraphStyle(
    "consider", fontName="Helvetica", fontSize=9, leading=14,
    textColor=C_MUTED, alignment=TA_JUSTIFY, leftIndent=10,
)
STYLE_TP = ParagraphStyle(
    "tp", fontName="Helvetica", fontSize=9.5, leading=15,
    textColor=C_BODY, alignment=TA_JUSTIFY,
)
STYLE_TP_RATIONALE = ParagraphStyle(
    "tpRationale", fontName="Helvetica-Oblique", fontSize=8, leading=12,
    textColor=C_MUTED, alignment=TA_LEFT,
)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def generate_batch_brief_pdf(
    batch_data: dict,
    candidates_data: List[dict],
    ref_code: Optional[str] = None,
) -> bytes:
    """
    Generate the Batch Analysis Brief PDF.

    Args:
        batch_data: Batch metadata (job_titles, avg_score, results, etc.)
        candidates_data: List of enriched candidate dicts, each containing:
            - name, current_role, location, years_experience
            - analysis: {overall_score, skill_match_score, experience_score,
                         depth_score, education_score, summary_text, recommendation,
                         strengths, gaps}
            - risk_flags: [{severity, title, description}]
            - interview_questions: [{question, rationale}]
            - skills: [{name, estimated_depth}]
        ref_code: Optional reference code for the brief.
    """
    buffer = io.BytesIO()
    try:
        c = canvas.Canvas(buffer, pagesize=letter)
        c.setTitle("VetLayer Batch Analysis Brief")
        c.setAuthor("VetLayer")

        today = datetime.now().strftime("%B %d, %Y")
        ref = ref_code or f"BA-{datetime.now().strftime('%Y')}-{batch_data.get('batch_id', 'XXXX')[:5].upper()}"

        state = {"today": today, "ref": ref}

        # Sort candidates by overall score descending
        candidates_data.sort(
            key=lambda x: x.get("analysis", {}).get("overall_score", 0),
            reverse=True,
        )

        # ═══════════════════════════════════════════════════════════════
        # PAGE 1: BATCH OVERVIEW
        # ═══════════════════════════════════════════════════════════════
        _draw_overview_page(c, batch_data, candidates_data, state)

        # ═══════════════════════════════════════════════════════════════
        # PAGES 2..N: INDIVIDUAL CANDIDATE DEEP DIVES
        # ═══════════════════════════════════════════════════════════════
        total_candidates = len(candidates_data)
        for idx, cand in enumerate(candidates_data):
            _draw_candidate_page(c, cand, idx, total_candidates, state)

        # ═══════════════════════════════════════════════════════════════
        # COMPARATIVE ANALYSIS PAGE
        # ═══════════════════════════════════════════════════════════════
        _draw_comparative_page(c, batch_data, candidates_data, state)

        # ═══════════════════════════════════════════════════════════════
        # FINAL PAGE(S): INTERVIEW QUESTIONS FOR ALL CANDIDATES
        # ═══════════════════════════════════════════════════════════════
        _draw_interview_questions_pages(c, candidates_data, state)

        c.save()
        pdf_bytes = buffer.getvalue()

        logger.info(f"Generated Batch Analysis Brief PDF ({len(pdf_bytes)} bytes, {total_candidates} candidates)")
        return pdf_bytes
    finally:
        buffer.close()


# ═══════════════════════════════════════════════════════════════════════════
# PAGE 1: BATCH OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════

def _draw_overview_page(c, batch_data: dict, candidates: List[dict], state: dict):
    _draw_page_bg(c)
    y = PAGE_H - MARGIN

    # Header
    c.setFont("Times-Bold", 18)
    c.setFillColor(C_BLACK)
    c.drawString(MARGIN, y, "VETLAYER")
    c.setFont("Helvetica", 7)
    c.setFillColor(C_FAINT)
    c.drawString(MARGIN, y - 14, "BATCH ANALYSIS BRIEF")

    c.setFont("Helvetica", 9)
    c.setFillColor(C_MUTED)
    c.drawRightString(PAGE_W - MARGIN, y, state["today"])
    c.setFont("Helvetica", 7)
    c.setFillColor(C_FAINT)
    c.drawRightString(PAGE_W - MARGIN, y - 14, f"REF {state['ref']}")
    y -= 54

    # Job title
    job_titles = batch_data.get("job_titles", [])
    title_text = job_titles[0].upper() if job_titles else "BATCH ANALYSIS"
    title_size = 24
    while title_size > 14:
        if c.stringWidth(title_text, "Times-Bold", title_size) <= CONTENT_W:
            break
        title_size -= 1
    c.setFont("Times-Bold", title_size)
    c.setFillColor(C_BLACK)
    c.drawCentredString(PAGE_W / 2, y, title_text)
    y -= 20

    # Subtitle
    subtitle = f"{len(candidates)} CANDIDATES EVALUATED"
    c.setFont("Helvetica", 9)
    c.setFillColor(C_MUTED)
    c.drawCentredString(PAGE_W / 2, y, subtitle)
    y -= 22

    _draw_sep(c, y)
    y -= 22

    # Key Metrics
    successful = [cd for cd in candidates if cd.get("analysis", {}).get("overall_score", 0) > 0]
    avg_score = 0
    strong_yes_count = 0
    if successful:
        avg_score = sum(cd["analysis"]["overall_score"] for cd in successful) / len(successful)
        strong_yes_count = sum(
            1 for cd in successful
            if cd["analysis"].get("recommendation", "").lower() in ("strong_yes", "strong yes")
        )

    avg_time_ms = batch_data.get("elapsed_ms", 0)
    avg_time_str = f"{avg_time_ms / 1000:.1f}s" if avg_time_ms else "N/A"

    metrics = [
        (str(len(candidates)), "CANDIDATES"),
        (f"{avg_score:.0f}%", "AVG SCORE"),
        (str(strong_yes_count), "STRONG YES"),
        (avg_time_str, "AVG TIME"),
    ]

    metric_spacing = CONTENT_W / len(metrics)
    for i, (val, label) in enumerate(metrics):
        mx = MARGIN + metric_spacing * i + metric_spacing / 2
        color = C_GREEN if "%" in val and avg_score >= 60 else C_AMBER if "%" in val else C_BLACK
        c.setFont("Times-Roman", 22)
        c.setFillColor(color)
        c.drawCentredString(mx, y, val)
        c.setFont("Helvetica", 6)
        c.setFillColor(C_FAINT)
        c.drawCentredString(mx, y - 14, label)
    y -= 42

    # Executive Summary
    summary = _build_batch_summary(batch_data, candidates)
    if summary:
        para = Paragraph(summary, STYLE_BODY)
        pw = CONTENT_W - 20
        _, h = para.wrap(pw, 200)
        para.drawOn(c, MARGIN + 10, y - h)
        y -= h + 12

    _draw_sep(c, y)
    y -= 18

    # Candidate Rankings Table
    y = _section_label_centered(c, "CANDIDATE RANKINGS", y)
    y = _draw_rankings_table(c, candidates, y)

    # Pool Strengths & Gaps (if room)
    if y > FOOTER_Y + 120:
        _draw_sep(c, y)
        y -= 14
        y = _draw_pool_strengths_gaps(c, candidates, y)

    _draw_footer(c, "BATCH ANALYSIS BRIEF")


def _build_batch_summary(batch_data: dict, candidates: List[dict]) -> str:
    """Build an executive summary paragraph from batch results."""
    n = len(candidates)
    job_titles = batch_data.get("job_titles", ["the role"])
    job = _sanitize(job_titles[0]) if job_titles else "the role"

    successful = [cd for cd in candidates if cd.get("analysis", {}).get("overall_score", 0) > 0]
    if not successful:
        return f"Batch analysis evaluated {n} candidates against {job}. No successful analyses were produced."

    avg = sum(cd["analysis"]["overall_score"] for cd in successful) / len(successful)
    top = successful[0] if successful else None
    top_name = top.get("name", "Unknown") if top else "Unknown"
    top_score = top["analysis"]["overall_score"] if top else 0

    strong_count = sum(
        1 for cd in successful
        if cd["analysis"].get("recommendation", "").lower() in ("strong_yes", "strong yes")
    )
    yes_count = sum(
        1 for cd in successful
        if cd["analysis"].get("recommendation", "").lower() in ("yes",)
    )

    parts = [f"This batch analysis evaluated {n} candidates against the {job} position."]
    parts.append(f"The candidate pool shows an average match score of {avg:.0f}%")
    if strong_count:
        parts.append(f"with {strong_count} candidate{'s' if strong_count > 1 else ''} receiving a strong recommendation")
    if yes_count:
        parts.append(f"and {yes_count} additional candidate{'s' if yes_count > 1 else ''} flagged for further evaluation")
    parts[1] = parts[1] + (", " + ", ".join(parts[2:]) + "." if len(parts) > 2 else ".")
    summary = " ".join(parts[:2])

    if top:
        summary += f" {_sanitize(top_name)} emerged as the top ranked candidate with a {top_score:.0f}% match."

    return summary


def _draw_rankings_table(c, candidates: List[dict], y: float) -> float:
    """Draw the candidate rankings table. Returns new y."""
    # Column layout — wider spacing to avoid overlap
    # rank(20) | name(160) | score_bar+num(90) | skills(40) | exp(36) | depth(40) | rec(75)
    # Total: 20+160+90+40+36+40+75 = 461, fits in CONTENT_W (500)
    col_rank_x = MARGIN
    col_name_x = MARGIN + 20
    col_score_x = MARGIN + 180
    col_skills_x = MARGIN + 275
    col_exp_x = MARGIN + 320
    col_depth_x = MARGIN + 358
    col_rec_x = MARGIN + 400

    # Header
    c.setFont("Helvetica-Bold", 7)
    c.setFillColor(C_MUTED)
    c.drawString(col_name_x, y, "CANDIDATE")
    c.drawCentredString(col_score_x + 40, y, "SCORE")
    c.drawCentredString(col_skills_x + 18, y, "SKILLS")
    c.drawCentredString(col_exp_x + 16, y, "EXP")
    c.drawCentredString(col_depth_x + 18, y, "DEPTH")
    c.drawCentredString(col_rec_x + 30, y, "REC")

    y -= 10
    c.setStrokeColor(C_BORDER)
    c.setLineWidth(0.5)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)
    y -= 16

    # Table rows
    for rank, cd in enumerate(candidates, 1):
        if y < FOOTER_Y + 30:
            break

        analysis = cd.get("analysis", {})
        score = analysis.get("overall_score", 0)
        skill_score = analysis.get("skill_match_score", 0)
        exp_score = analysis.get("experience_score", 0)
        depth_score = analysis.get("depth_score", 0)
        rec = analysis.get("recommendation", "")
        name = _clean(cd.get("name", "Unknown"))
        role = _clean(cd.get("current_role", ""))
        years = cd.get("years_experience")
        years_str = f"{years:.0f}y exp" if years and years >= 1 else "<1y exp" if years is not None else ""
        role_line = f"{role} | {years_str}" if role and years_str else role or years_str

        # Rank number
        c.setFont("Times-Roman", 14)
        c.setFillColor(C_FAINT)
        c.drawString(col_rank_x, y - 2, str(rank))

        # Name + role
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(C_BLACK)
        max_name_w = col_score_x - col_name_x - 8
        display_name = name
        while c.stringWidth(display_name, "Helvetica-Bold", 9) > max_name_w and len(display_name) > 10:
            display_name = display_name[:-4] + "..."
        c.drawString(col_name_x, y, display_name)

        if role_line:
            c.setFont("Helvetica", 7.5)
            c.setFillColor(C_MUTED)
            display_role = role_line
            while c.stringWidth(display_role, "Helvetica", 7.5) > max_name_w and len(display_role) > 10:
                display_role = display_role[:-4] + "..."
            c.drawString(col_name_x, y - 10, display_role)

        # Score: bar + number (bar is 40px wide, then gap, then number)
        bar_x = col_score_x
        bar_w = 40
        bar_y = y - 1
        c.setFillColor(HexColor("#eae6df"))
        c.roundRect(bar_x, bar_y, bar_w, 4, 2, fill=1, stroke=0)
        fill_w = bar_w * min(score / 100, 1.0)
        color = _score_color(score)
        c.setFillColor(color)
        if fill_w > 1:
            c.roundRect(bar_x, bar_y, fill_w, 4, 2, fill=1, stroke=0)
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(color)
        c.drawString(bar_x + bar_w + 6, y - 1, f"{score:.0f}")

        # Sub-scores — centered in their columns
        for sx, val in [
            (col_skills_x + 18, skill_score),
            (col_exp_x + 16, exp_score),
            (col_depth_x + 18, depth_score),
        ]:
            sc = _score_color(val)
            c.setFont("Helvetica-Bold", 8.5)
            c.setFillColor(sc)
            c.drawCentredString(sx, y - 1, f"{val:.0f}")

        # Recommendation pill
        _draw_rec_pill(c, rec, col_rec_x, y - 4, 60)

        # Row separator
        y -= 28
        c.setStrokeColor(HexColor("#eae6df"))
        c.setLineWidth(0.3)
        c.line(MARGIN, y + 6, PAGE_W - MARGIN, y + 6)

    return y


# ═══════════════════════════════════════════════════════════════════════════
# PAGES 2..N: INDIVIDUAL CANDIDATE DEEP DIVE
# ═══════════════════════════════════════════════════════════════════════════

def _draw_candidate_page(c, cand: dict, idx: int, total: int, state: dict):
    c.showPage()
    _draw_page_bg(c)
    y = PAGE_H - MARGIN

    # Mini header
    c.setFont("Times-Bold", 12)
    c.setFillColor(C_BLACK)
    c.drawString(MARGIN, y, "VETLAYER")
    c.setFont("Helvetica", 9)
    c.setFillColor(C_MUTED)
    c.drawRightString(PAGE_W - MARGIN, y, state["today"])
    y -= 24

    # Candidate badge
    c.setFont("Helvetica", 7)
    c.setFillColor(C_FAINT)
    badge = f"CANDIDATE {idx + 1} OF {total}  ·  RANK #{idx + 1}"
    c.drawCentredString(PAGE_W / 2, y, badge)
    y -= 22

    # Candidate name
    analysis = cand.get("analysis", {})
    name = _clean(cand.get("name", "Unknown")).upper()
    name_size = 22
    while name_size > 14:
        if c.stringWidth(name, "Times-Bold", name_size) <= CONTENT_W:
            break
        name_size -= 1
    c.setFont("Times-Bold", name_size)
    c.setFillColor(C_BLACK)
    c.drawCentredString(PAGE_W / 2, y, name)
    y -= 18

    # Subtitle
    role = _clean(cand.get("current_role", ""))
    years = cand.get("years_experience")
    location = _clean(cand.get("location", ""))
    years_str = f"{years:.0f} YEARS EXPERIENCE" if years and years >= 1 else "<1 YEAR EXPERIENCE" if years is not None else ""
    sub_parts = [p for p in [role.upper(), years_str, location.upper()] if p]
    subtitle = "  |  ".join(sub_parts)
    if subtitle:
        sub_size = 9
        while sub_size > 6:
            if c.stringWidth(subtitle, "Helvetica", sub_size) <= CONTENT_W:
                break
            sub_size -= 0.5
        c.setFont("Helvetica", sub_size)
        c.setFillColor(C_MUTED)
        c.drawCentredString(PAGE_W / 2, y, subtitle)
        y -= 16

    _draw_sep(c, y)
    y -= 20

    # Score metrics row
    overall = analysis.get("overall_score", 0)
    skill_match = analysis.get("skill_match_score", 0)
    exp_score = analysis.get("experience_score", 0)
    depth = analysis.get("depth_score", 0)
    edu = analysis.get("education_score", 0)

    score_metrics = [
        (f"{overall:.0f}", "OVERALL SCORE", 28, _score_color(overall)),
        (f"{skill_match:.0f}", "SKILL MATCH", 22, C_BLACK),
        (f"{exp_score:.0f}", "EXPERIENCE", 22, C_BLACK),
        (f"{depth:.0f}", "DEPTH", 22, C_BLACK),
        (f"{edu:.0f}", "EDUCATION", 22, C_BLACK),
    ]
    metric_spacing = CONTENT_W / len(score_metrics)
    for i, (val, label, fsize, color) in enumerate(score_metrics):
        mx = MARGIN + metric_spacing * i + metric_spacing / 2
        c.setFont("Times-Roman", fsize)
        c.setFillColor(color)
        c.drawCentredString(mx, y, val)
        c.setFont("Helvetica", 6)
        c.setFillColor(C_FAINT)
        c.drawCentredString(mx, y - 14, label)
    y -= 38

    # Recommendation pill centered
    rec = analysis.get("recommendation", "")
    if rec:
        pill_text = rec.upper().replace("_", " ")
        pill_w = c.stringWidth(pill_text, "Helvetica-Bold", 8) + 20
        _draw_rec_pill(c, rec, PAGE_W / 2 - pill_w / 2, y - 2, pill_w)
        y -= 22

    # Analysis Summary
    summary = analysis.get("summary_text", "")
    if summary:
        para = Paragraph(_sanitize(summary), STYLE_BODY)
        pw = CONTENT_W - 20
        _, h = para.wrap(pw, 200)
        para.drawOn(c, MARGIN + 10, y - h)
        y -= h + 12

    _draw_sep(c, y)
    y -= 18

    # Two columns: Score breakdown + Risk flags | Key Strengths + Skill Gaps
    y_left = y
    y_right = y

    # LEFT: Score Breakdown
    y_left = _section_label(c, "SCORE BREAKDOWN", LEFT_X, COL_W, y_left)
    breakdowns = [
        ("Skill Match", skill_match),
        ("Experience", exp_score),
        ("Depth", depth),
        ("Education", edu),
    ]
    for label, val in breakdowns:
        y_left = _draw_breakdown_bar(c, label, val, LEFT_X, COL_W, y_left)
    y_left -= 10

    # LEFT: Risk Flags
    risk_flags = cand.get("risk_flags", [])
    if risk_flags:
        y_left = _section_label(c, "RISK FLAGS", LEFT_X, COL_W, y_left)
        for rf in risk_flags[:3]:
            if y_left < FOOTER_Y + 30:
                break
            severity = rf.get("severity", "medium")
            title = _sanitize(rf.get("title", ""))
            desc = _sanitize(rf.get("description", ""))
            y_left = _draw_risk_flag(c, severity, title, desc, LEFT_X, COL_W, y_left)

    # RIGHT: Key Strengths
    y_right = _section_label(c, "KEY STRENGTHS", RIGHT_X, COL_W, y_right)
    strengths_items = _extract_text_items(analysis.get("strengths"))
    for s in strengths_items[:4]:
        if y_right < FOOTER_Y + 30:
            break
        para = Paragraph(_sanitize(s), STYLE_STRENGTH)
        _, h = para.wrap(COL_W, 100)
        para.drawOn(c, RIGHT_X, y_right - h)
        y_right -= h + 4
        c.setStrokeColor(HexColor("#eae6df"))
        c.setLineWidth(0.4)
        c.line(RIGHT_X, y_right + 2, RIGHT_X + COL_W, y_right + 2)
    y_right -= 10

    # RIGHT: Skill Gaps
    gaps_items = _extract_text_items(analysis.get("gaps"))
    if gaps_items:
        y_right = _section_label(c, "SKILL GAPS", RIGHT_X, COL_W, y_right)
        for g in gaps_items[:3]:
            if y_right < FOOTER_Y + 30:
                break
            text = _sanitize(g)
            para = Paragraph(text, STYLE_GAP)
            _, h = para.wrap(COL_W, 100)
            para.drawOn(c, RIGHT_X, y_right - h)
            y_right -= h + 2

    y = min(y_left, y_right) - 8

    # Interview Questions (if room)
    questions = cand.get("interview_questions", [])
    rec_lower = rec.lower().replace("_", " ") if rec else ""
    if rec_lower == "no" or rec_lower == "strong no":
        # For "no" recommendations, show a note instead
        if y > FOOTER_Y + 50:
            _draw_sep(c, y)
            y -= 18
            y = _section_label_centered(c, "NOTES", y)
            para = Paragraph(
                f"<i>No interview recommended for this role. Consider redirecting to alternative openings if available.</i>",
                STYLE_BODY,
            )
            _, h = para.wrap(CONTENT_W - 20, 100)
            para.drawOn(c, MARGIN + 10, y - h)
    elif questions and y > FOOTER_Y + 80:
        _draw_sep(c, y)
        y -= 18
        y = _section_label_centered(c, "RECOMMENDED INTERVIEW QUESTIONS", y)
        for i, q in enumerate(questions[:3], 1):
            if y < FOOTER_Y + 30:
                break
            c.setFont("Times-Roman", 16)
            c.setFillColor(C_FAINT)
            c.drawString(MARGIN, y, str(i))

            q_text = _sanitize(q.get("question", ""))
            rationale = _sanitize(q.get("rationale", ""))

            para = Paragraph(q_text, STYLE_TP)
            pw = CONTENT_W - 30
            _, h = para.wrap(pw, 200)
            para.drawOn(c, MARGIN + 24, y - h + 10)
            y -= max(h, 14) + 4

            if rationale:
                para = Paragraph(rationale, STYLE_TP_RATIONALE)
                _, h = para.wrap(pw, 100)
                para.drawOn(c, MARGIN + 24, y - h + 8)
                y -= h + 6

    _draw_footer(c, "BATCH ANALYSIS BRIEF")


# ═══════════════════════════════════════════════════════════════════════════
# FINAL PAGE: COMPARATIVE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

def _draw_comparative_page(c, batch_data: dict, candidates: List[dict], state: dict):
    c.showPage()
    _draw_page_bg(c)
    y = PAGE_H - MARGIN

    # Mini header
    c.setFont("Times-Bold", 12)
    c.setFillColor(C_BLACK)
    c.drawString(MARGIN, y, "VETLAYER")
    c.setFont("Helvetica", 9)
    c.setFillColor(C_MUTED)
    c.drawRightString(PAGE_W - MARGIN, y, state["today"])
    y -= 36

    # Skill Coverage Matrix
    y = _section_label_centered(c, "SKILL COVERAGE MATRIX", y)
    y = _draw_skill_matrix(c, candidates, y)

    if y > FOOTER_Y + 60:
        _draw_sep(c, y)
        y -= 18

    # Considerations
    if y > FOOTER_Y + 80:
        y = _section_label_centered(c, "CONSIDERATIONS", y)
        considerations = _build_considerations(candidates)
        for con in considerations[:3]:
            if y < FOOTER_Y + 40:
                break
            para = Paragraph(con, STYLE_CONSIDER)
            _, h = para.wrap(CONTENT_W - 20, 100)
            # Left border accent
            c.setStrokeColor(C_BORDER)
            c.setLineWidth(1.5)
            c.line(MARGIN, y - h + 2, MARGIN, y + 2)
            c.setFillColor(C_CONSIDER_BG)
            c.rect(MARGIN + 2, y - h, CONTENT_W - 2, h + 4, fill=1, stroke=0)
            para.drawOn(c, MARGIN + 12, y - h + 2)
            y -= h + 12

    if y > FOOTER_Y + 60:
        _draw_sep(c, y)
        y -= 18

    # Recruiter Recommendation
    if y > FOOTER_Y + 60:
        y = _section_label_centered(c, "RECRUITER RECOMMENDATION", y)
        recs = _build_recommendations(candidates)
        for rec_text in recs:
            if y < FOOTER_Y + 30:
                break
            para = Paragraph(rec_text, STYLE_BODY)
            _, h = para.wrap(CONTENT_W - 20, 100)
            para.drawOn(c, MARGIN + 10, y - h)
            y -= h + 6

    _draw_footer(c, "BATCH ANALYSIS BRIEF")


# ═══════════════════════════════════════════════════════════════════════════
# FINAL PAGE(S): INTERVIEW QUESTIONS
# ═══════════════════════════════════════════════════════════════════════════

STYLE_IQ_QUESTION = ParagraphStyle(
    "iqQuestion", fontName="Helvetica", fontSize=9.5, leading=14.5,
    textColor=C_DARK, alignment=TA_LEFT,
)
STYLE_IQ_RATIONALE = ParagraphStyle(
    "iqRationale", fontName="Helvetica-Oblique", fontSize=8, leading=12,
    textColor=C_MUTED, alignment=TA_LEFT,
)
STYLE_IQ_CATEGORY = ParagraphStyle(
    "iqCategory", fontName="Helvetica-Bold", fontSize=7, leading=10,
    textColor=C_FAINT, alignment=TA_LEFT,
)


def _draw_interview_questions_pages(c, candidates: List[dict], state: dict):
    """Draw dedicated interview questions page(s) — one section per candidate."""
    # Filter candidates that have interview questions
    cands_with_qs = [
        cd for cd in candidates
        if cd.get("interview_questions") and len(cd.get("interview_questions", [])) > 0
    ]
    if not cands_with_qs:
        return  # No interview questions to show

    # Start a new page
    c.showPage()
    _draw_page_bg(c)
    y = PAGE_H - MARGIN

    # Mini header
    c.setFont("Times-Bold", 12)
    c.setFillColor(C_BLACK)
    c.drawString(MARGIN, y, "VETLAYER")
    c.setFont("Helvetica", 9)
    c.setFillColor(C_MUTED)
    c.drawRightString(PAGE_W - MARGIN, y, state["today"])
    y -= 30

    # Page title
    c.setFont("Times-Bold", 18)
    c.setFillColor(C_BLACK)
    c.drawCentredString(PAGE_W / 2, y, "INTERVIEW PREPARATION")
    y -= 16

    c.setFont("Helvetica", 8)
    c.setFillColor(C_MUTED)
    c.drawCentredString(PAGE_W / 2, y, f"Recommended questions for {len(cands_with_qs)} candidates")
    y -= 14

    _draw_sep(c, y)
    y -= 24

    # Iterate through each candidate's questions
    for cand_idx, cd in enumerate(cands_with_qs):
        name = _clean(cd.get("name", "Unknown"))
        rec = cd.get("analysis", {}).get("recommendation", "")
        overall = cd.get("analysis", {}).get("overall_score", 0)
        questions = cd.get("interview_questions", [])

        # Check if we need a new page for this candidate section
        # Need ~100px minimum for candidate header + at least one question
        if y < FOOTER_Y + 120:
            _draw_footer(c, "BATCH ANALYSIS BRIEF")
            c.showPage()
            _draw_page_bg(c)
            y = PAGE_H - MARGIN

            # Continuation header
            c.setFont("Times-Bold", 12)
            c.setFillColor(C_BLACK)
            c.drawString(MARGIN, y, "VETLAYER")
            c.setFont("Helvetica", 9)
            c.setFillColor(C_MUTED)
            c.drawRightString(PAGE_W - MARGIN, y, state["today"])
            y -= 28

            c.setFont("Helvetica", 7)
            c.setFillColor(C_FAINT)
            c.drawCentredString(PAGE_W / 2, y, "INTERVIEW PREPARATION (CONTINUED)")
            y -= 22

        # ── Candidate header block ──────────────────────────────────
        # Background accent bar
        header_h = 32
        c.setFillColor(C_ACCENT_BG)
        c.roundRect(MARGIN, y - header_h + 10, CONTENT_W, header_h, 3, fill=1, stroke=0)

        # Rank number
        rank = cand_idx + 1
        c.setFont("Times-Roman", 20)
        c.setFillColor(C_FAINT)
        c.drawString(MARGIN + 8, y - 10, str(rank))

        # Candidate name
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(C_BLACK)
        c.drawString(MARGIN + 32, y - 2, name.upper())

        # Role + score line
        role = _clean(cd.get("current_role", ""))
        score_str = f"Score: {overall:.0f}%"
        sub_text = f"{role}  |  {score_str}" if role else score_str
        c.setFont("Helvetica", 8)
        c.setFillColor(C_MUTED)
        c.drawString(MARGIN + 32, y - 14, sub_text)

        # Recommendation pill on right
        if rec:
            pill_text = rec.upper().replace("_", " ")
            pill_w = c.stringWidth(pill_text, "Helvetica-Bold", 7) + 16
            _draw_rec_pill(c, rec, PAGE_W - MARGIN - pill_w - 8, y - 12, pill_w)

        y -= header_h + 12

        # ── Questions ───────────────────────────────────────────────
        for qi, q in enumerate(questions, 1):
            if y < FOOTER_Y + 50:
                _draw_footer(c, "BATCH ANALYSIS BRIEF")
                c.showPage()
                _draw_page_bg(c)
                y = PAGE_H - MARGIN

                c.setFont("Times-Bold", 12)
                c.setFillColor(C_BLACK)
                c.drawString(MARGIN, y, "VETLAYER")
                c.setFont("Helvetica", 9)
                c.setFillColor(C_MUTED)
                c.drawRightString(PAGE_W - MARGIN, y, state["today"])
                y -= 28

                c.setFont("Helvetica", 7)
                c.setFillColor(C_FAINT)
                c.drawCentredString(PAGE_W / 2, y, f"INTERVIEW PREPARATION — {name.upper()} (CONTINUED)")
                y -= 22

            q_text = q.get("question", "")
            rationale = q.get("rationale", "")
            category = q.get("category", "")

            # Question number
            c.setFont("Times-Roman", 14)
            c.setFillColor(C_FAINT)
            c.drawString(MARGIN + 4, y, str(qi))

            # Category badge (if present)
            text_x = MARGIN + 28
            text_w = CONTENT_W - 36
            if category:
                cat_text = category.upper()
                cat_w = c.stringWidth(cat_text, "Helvetica-Bold", 6) + 10
                c.setFillColor(C_ACCENT_BG)
                c.roundRect(text_x, y + 2, cat_w, 10, 2, fill=1, stroke=0)
                c.setFont("Helvetica-Bold", 6)
                c.setFillColor(C_MUTED)
                c.drawString(text_x + 5, y + 4, cat_text)
                y -= 14

            # Question text
            para = Paragraph(_sanitize(q_text), STYLE_IQ_QUESTION)
            _, h = para.wrap(text_w, 200)
            para.drawOn(c, text_x, y - h + 10)
            y -= max(h, 14) + 4

            # Rationale
            if rationale:
                para = Paragraph(_sanitize(rationale), STYLE_IQ_RATIONALE)
                _, h = para.wrap(text_w, 100)
                para.drawOn(c, text_x, y - h + 8)
                y -= h + 4

            # Light separator between questions
            y -= 2
            c.setStrokeColor(HexColor("#eae6df"))
            c.setLineWidth(0.3)
            c.line(text_x, y + 2, PAGE_W - MARGIN, y + 2)
            y -= 8

        # Separator between candidates
        if cand_idx < len(cands_with_qs) - 1:
            y -= 6
            _draw_sep(c, y)
            y -= 20

    _draw_footer(c, "BATCH ANALYSIS BRIEF")


def _draw_skill_matrix(c, candidates: List[dict], y: float) -> float:
    """Draw a skill coverage matrix comparing candidates' skills."""
    # Collect all unique skills across candidates (from their analysis skills)
    skill_scores = {}  # skill_name -> {cand_name: depth}
    for cd in candidates:
        name = cd.get("name", "Unknown")
        skills = cd.get("skills", [])
        for s in skills:
            sname = s.get("name", "")
            depth = s.get("estimated_depth", 0)
            if sname and depth > 0:
                if sname not in skill_scores:
                    skill_scores[sname] = {}
                skill_scores[sname][name] = depth

    if not skill_scores:
        c.setFont("Helvetica", 9)
        c.setFillColor(C_MUTED)
        c.drawCentredString(PAGE_W / 2, y, "No skill data available for comparison.")
        return y - 20

    # Pick top 6 skills by frequency
    sorted_skills = sorted(skill_scores.items(), key=lambda x: len(x[1]), reverse=True)[:6]

    # Table dimensions
    name_col_w = 100
    max_cands = min(len(candidates), 6)
    cand_col_w = (CONTENT_W - name_col_w) / max_cands if max_cands else 60

    # Header row — candidate names
    c.setFont("Helvetica-Bold", 7)
    c.setFillColor(C_MUTED)
    c.drawString(MARGIN, y, "REQUIRED SKILL")
    for i, cd in enumerate(candidates[:max_cands]):
        cand_name = cd.get("name", "?")
        # Abbreviate: "Kevin T" → "Kevin T", "Akshat Prahaladh" → "Akshat P"
        parts = cand_name.split()
        short_name = parts[0] + (" " + parts[1][0] if len(parts) > 1 else "")
        cx = MARGIN + name_col_w + cand_col_w * i + cand_col_w / 2
        c.drawCentredString(cx, y, short_name.upper())

    y -= 8
    c.setStrokeColor(C_BORDER)
    c.setLineWidth(0.5)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)
    y -= 16

    # Skill rows
    for skill_name, cand_depths in sorted_skills:
        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColor(C_DARK)
        display_skill = skill_name[:18] + "..." if len(skill_name) > 21 else skill_name
        c.drawString(MARGIN, y, display_skill)

        for i, cd in enumerate(candidates[:max_cands]):
            cand_name = cd.get("name", "Unknown")
            depth = cand_depths.get(cand_name, 0)
            cx = MARGIN + name_col_w + cand_col_w * i + cand_col_w / 2
            color = _depth_color(depth)
            c.setFont("Helvetica-Bold", 8)
            c.setFillColor(color)
            c.drawCentredString(cx, y, f"{depth}/5" if depth > 0 else "0/5")

        y -= 16
        c.setStrokeColor(HexColor("#eae6df"))
        c.setLineWidth(0.3)
        c.line(MARGIN, y + 5, PAGE_W - MARGIN, y + 5)

    return y


def _build_considerations(candidates: List[dict]) -> List[str]:
    """Build batch-level considerations from analysis data."""
    considerations = []

    # Check for common gaps
    all_gaps = []
    for cd in candidates:
        gaps = cd.get("analysis", {}).get("gaps", [])
        if isinstance(gaps, list):
            all_gaps.extend(gaps)

    if all_gaps:
        # Find most common gap themes
        gap_lower = [g.lower() if isinstance(g, str) else "" for g in all_gaps]
        common_themes = set()
        for g in gap_lower:
            if "cloud" in g or "aws" in g or "devops" in g:
                common_themes.add("cloud/DevOps")
            elif "system design" in g or "architecture" in g or "scale" in g:
                common_themes.add("system design at scale")
            elif "testing" in g or "ci/cd" in g:
                common_themes.add("testing and CI/CD")
        if common_themes:
            themes_str = ", ".join(common_themes)
            considerations.append(
                f"<b>Common skill gaps across pool:</b> Multiple candidates show limited experience with {themes_str}. "
                f"Consider expanding the search or pairing the hire with complementary team members."
            )

    # Check seniority spread
    years_list = [cd.get("years_experience", 0) for cd in candidates if cd.get("years_experience")]
    if years_list:
        avg_years = sum(years_list) / len(years_list)
        if avg_years < 4:
            considerations.append(
                f"<b>Seniority calibration:</b> The average experience level in this pool is {avg_years:.1f} years. "
                f"Verify the role truly requires senior-level experience or if a strong mid-level candidate "
                f"with high growth potential could be a better long-term investment."
            )

    # Check score distribution
    scores = [cd.get("analysis", {}).get("overall_score", 0) for cd in candidates]
    if scores:
        high = sum(1 for s in scores if s >= 70)
        low = sum(1 for s in scores if s < 40)
        if low > len(scores) / 2:
            considerations.append(
                f"<b>Pool quality concern:</b> {low} of {len(scores)} candidates scored below 40%. "
                f"The sourcing pipeline may need adjustment to attract better-matched candidates."
            )

    if not considerations:
        considerations.append(
            "<b>Overall assessment:</b> The candidate pool shows reasonable alignment with the role requirements. "
            "Proceed with interviews for recommended candidates."
        )

    return considerations


def _build_recommendations(candidates: List[dict]) -> List[str]:
    """Build advance/hold/pass recommendation groups."""
    advance = []
    hold = []
    pass_list = []

    for cd in candidates:
        name = cd.get("name", "Unknown")
        score = cd.get("analysis", {}).get("overall_score", 0)
        rec = cd.get("analysis", {}).get("recommendation", "").lower().replace("_", " ")

        entry = f"{_sanitize(name)} ({score:.0f}%)"
        if rec in ("strong yes", "strong_yes"):
            advance.append(entry)
        elif rec == "yes":
            hold.append(entry)
        elif rec == "maybe":
            hold.append(entry)
        else:
            pass_list.append(entry)

    recs = []
    if advance:
        recs.append(f"<b>Advance to interview:</b> {', '.join(advance)}")
    if hold:
        recs.append(f"<b>Hold for review:</b> {', '.join(hold)}")
    if pass_list:
        recs.append(f"<b>Pass:</b> {', '.join(pass_list)}")

    return recs


def _extract_text_items(data) -> List[str]:
    """Extract a flat list of strings from strengths/gaps which may be list, dict, or None."""
    if not data:
        return []
    if isinstance(data, list):
        return [str(s) for s in data if s]
    if isinstance(data, dict):
        # Could be {"category": "description"} — use values if they're strings, else keys
        items = []
        for k, v in data.items():
            if isinstance(v, str):
                items.append(v)
            else:
                items.append(str(k))
        return items
    return []


def _truncate_for_column(text: str, max_chars: int = 200) -> str:
    """Truncate text to fit in a column, keeping it readable."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 3].rsplit(" ", 1)[0] + "..."


def _draw_pool_strengths_gaps(c, candidates: List[dict], y: float) -> float:
    """Draw pool strengths and gaps in two columns."""
    y_left = y
    y_right = y

    # LEFT: Pool Strengths
    y_left = _section_label(c, "POOL STRENGTHS", LEFT_X, COL_W, y_left)
    strengths = []
    seen_strengths = set()
    for cd in candidates:
        raw = cd.get("analysis", {}).get("strengths")
        items = _extract_text_items(raw)
        for s in items[:2]:
            # Deduplicate by first 40 chars
            key = s[:40].lower()
            if key not in seen_strengths:
                seen_strengths.add(key)
                strengths.append(_truncate_for_column(s))
    for s_text in strengths[:4]:
        if y_left < FOOTER_Y + 20:
            break
        para = Paragraph(_sanitize(s_text), STYLE_STRENGTH)
        _, h = para.wrap(COL_W, 100)
        para.drawOn(c, LEFT_X, y_left - h)
        y_left -= h + 4
        c.setStrokeColor(HexColor("#eae6df"))
        c.setLineWidth(0.3)
        c.line(LEFT_X, y_left + 2, LEFT_X + COL_W, y_left + 2)

    # RIGHT: Common Gaps
    y_right = _section_label(c, "COMMON GAPS", RIGHT_X, COL_W, y_right)
    gaps = []
    seen_gaps = set()
    for cd in candidates:
        raw = cd.get("analysis", {}).get("gaps")
        items = _extract_text_items(raw)
        for g in items[:2]:
            key = g[:40].lower()
            if key not in seen_gaps:
                seen_gaps.add(key)
                gaps.append(_truncate_for_column(g))
    for g_text in gaps[:4]:
        if y_right < FOOTER_Y + 20:
            break
        para = Paragraph(_sanitize(g_text), STYLE_GAP)
        _, h = para.wrap(COL_W, 100)
        para.drawOn(c, RIGHT_X, y_right - h)
        y_right -= h + 4
        c.setStrokeColor(HexColor("#eae6df"))
        c.setLineWidth(0.3)
        c.line(RIGHT_X, y_right + 2, RIGHT_X + COL_W, y_right + 2)

    return min(y_left, y_right) - 6


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def _sanitize(text: str) -> str:
    """Sanitize text for use in reportlab Paragraph (XML context)."""
    if not text:
        return text
    text = text.replace("\u2014", ", ")
    text = text.replace("\u2013", ", ")
    text = text.replace(" -- ", ", ")
    text = text.replace("--", ", ")
    text = text.replace(" - ", ", ")
    # Escape XML special chars for Paragraph
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def _clean(text: str) -> str:
    """Clean text for use in canvas drawString (no XML escaping needed)."""
    if not text:
        return text
    text = text.replace("\u2014", ", ")
    text = text.replace("\u2013", ", ")
    text = text.replace(" -- ", ", ")
    text = text.replace("--", ", ")
    text = text.replace(" - ", ", ")
    return text


def _draw_page_bg(c):
    c.setFillColor(C_BG)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setStrokeColor(C_BORDER)
    c.setLineWidth(0.5)
    c.rect(INNER_BORDER, INNER_BORDER,
           PAGE_W - 2 * INNER_BORDER, PAGE_H - 2 * INNER_BORDER,
           fill=0, stroke=1)


def _draw_sep(c, y):
    c.setStrokeColor(C_BORDER)
    c.setLineWidth(0.3)
    c.line(MARGIN + 80, y, PAGE_W - MARGIN - 80, y)


def _draw_footer(c, doc_type="BATCH ANALYSIS BRIEF"):
    c.setFont("Helvetica", 6)
    c.setFillColor(C_FAINT)
    c.drawCentredString(
        PAGE_W / 2, 32,
        f"CONFIDENTIAL  |  FOR INTERNAL RECRUITER USE ONLY  |  VETLAYER {doc_type}"
    )


def _section_label(c, text, x, width, y):
    c.setFont("Helvetica-Bold", 6.5)
    c.setFillColor(C_BLACK)
    c.drawCentredString(x + width / 2, y, text)
    return y - 16


def _section_label_centered(c, text, y):
    c.setFont("Helvetica-Bold", 6.5)
    c.setFillColor(C_BLACK)
    c.drawCentredString(PAGE_W / 2, y, text)
    return y - 16


def _score_color(score: float) -> HexColor:
    if score >= 70:
        return C_GREEN
    elif score >= 40:
        return C_AMBER
    else:
        return C_RED


def _depth_color(depth: int) -> HexColor:
    if depth >= 3:
        return C_GREEN
    elif depth >= 2:
        return C_AMBER
    else:
        return C_RED


def _draw_breakdown_bar(c, label: str, value: float, x: float, width: float, y: float) -> float:
    """Draw a single score breakdown bar row."""
    label_w = 70
    bar_start = x + label_w + 8
    bar_w = width - label_w - 38
    val_x = x + width

    c.setFont("Helvetica", 8)
    c.setFillColor(C_MUTED)
    c.drawRightString(x + label_w, y, label)

    # Background bar
    c.setFillColor(HexColor("#eae6df"))
    c.roundRect(bar_start, y - 1, bar_w, 6, 3, fill=1, stroke=0)

    # Fill bar
    fill_w = bar_w * min(value / 100, 1.0)
    c.setFillColor(_score_color(value))
    if fill_w > 0:
        c.roundRect(bar_start, y - 1, fill_w, 6, 3, fill=1, stroke=0)

    # Value text
    c.setFont("Helvetica", 8)
    c.setFillColor(C_DARK)
    c.drawString(val_x - 28, y, f"{value:.0f}%")

    return y - 16


def _draw_risk_flag(c, severity: str, title: str, desc: str, x: float, width: float, y: float) -> float:
    """Draw a single risk flag."""
    # Severity badge
    sev_lower = severity.lower()
    if sev_lower == "high" or sev_lower == "critical":
        badge_bg, badge_color = C_RED_BG, C_RED
    elif sev_lower == "medium":
        badge_bg, badge_color = C_AMBER_BG, C_AMBER
    else:
        badge_bg, badge_color = C_ACCENT_BG, C_MUTED

    badge_text = sev_lower.upper()
    badge_w = c.stringWidth(badge_text, "Helvetica-Bold", 6) + 8

    c.setFillColor(badge_bg)
    c.roundRect(x, y - 2, badge_w, 10, 2, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 6)
    c.setFillColor(badge_color)
    c.drawString(x + 4, y, badge_text)

    # Flag text
    flag_text = f"<b>{title}:</b> {desc}" if title and desc else title or desc
    para = Paragraph(flag_text, ParagraphStyle(
        "flagText", fontName="Helvetica", fontSize=8.5, leading=13,
        textColor=C_BODY, alignment=TA_LEFT,
    ))
    text_x = x + badge_w + 6
    text_w = width - badge_w - 6
    _, h = para.wrap(text_w, 80)
    para.drawOn(c, text_x, y - h + 8)
    return y - max(h, 12) - 4


def _draw_rec_pill(c, recommendation: str, x: float, y: float, width: float):
    """Draw a recommendation pill."""
    rec_lower = recommendation.lower().replace("_", " ")
    pill_text = rec_lower.upper()

    if rec_lower in ("strong yes", "strong_yes"):
        bg, fg = C_GREEN_BG, C_GREEN
        pill_text = "STRONG YES"
    elif rec_lower == "yes":
        bg, fg = C_GREEN_BG, C_GREEN
    elif rec_lower == "maybe":
        bg, fg = C_AMBER_BG, C_AMBER
    else:
        bg, fg = C_RED_BG, C_RED
        pill_text = "NO"

    h = 14
    c.setFillColor(bg)
    c.roundRect(x, y, width, h, 2, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 7)
    c.setFillColor(fg)
    c.drawCentredString(x + width / 2, y + 4, pill_text)
