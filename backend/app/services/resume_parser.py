"""
Resume Parser Service
Extracts structured data from raw resume text (PDF/DOCX/TXT).

Two-phase approach for speed:
  1. Quick regex/heuristic parse (instant, <50ms) — name, email, phone, location
  2. Full LLM parse (background, ~10-15s) — experience, education, skills, etc.
"""

import io
import re
import json
import asyncio
import logging
from typing import Optional, Tuple
from dataclasses import dataclass, field, asdict

from app.utils.llm_client import llm_client

logger = logging.getLogger(__name__)

# ── LLM prompt for structured resume extraction ───────────────────────
RESUME_EXTRACTION_PROMPT = """Parse this resume into structured JSON. Be thorough and precise.

Return JSON with these keys:
- name, email, phone, location, summary (string or null)
- experience: [{company, title, start_date, end_date, description (include FULL role description text), technologies:[]}]
- education: [{institution, degree, field, graduation_date, gpa}] — one entry per distinct institution+degree pair, highest degree first. Never duplicate entries. For Indian education: map Class X/10th/SSLC and Class XII/12th/HSC to their correct schools.
- skills_mentioned: [all technical and professional skills found anywhere in the resume]
- certifications: [{name, issuer, date}]
- projects: [{name, description, technologies:[], url}]
- links: [{url, label}]
- years_experience: float (calculate from earliest work start date to now; 0 if no work experience)
- education_level: "Bachelor's"/"Master's"/"PhD"/"Diploma"/"High School" or null
- current_role, current_company: most recent role and company as strings

Use null (not "") for missing fields. Be thorough with experience descriptions — include all bullet points and achievements."""


# ── Quick regex patterns for instant extraction ────────────────────────
_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
_PHONE_RE = re.compile(r'(?:\+?\d{1,3}[\s\-.]?)?\(?\d{2,4}\)?[\s\-.]?\d{3,4}[\s\-.]?\d{3,4}')
_LINKEDIN_RE = re.compile(r'linkedin\.com/in/[\w\-]+', re.IGNORECASE)
_URL_RE = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+')

# Location patterns (City, State / City, Country)
_LOCATION_RE = re.compile(
    r'(?:^|\n)\s*([A-Z][a-zA-Z\s]+,\s*(?:[A-Z]{2}|[A-Z][a-zA-Z\s]+))\s*(?:\n|$|[|\-])',
    re.MULTILINE,
)


@dataclass
class ParsedResume:
    """Structured output from resume parsing."""
    name: str = ""
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    summary: Optional[str] = None
    experience: list = field(default_factory=list)
    education: list = field(default_factory=list)
    skills_mentioned: list = field(default_factory=list)
    certifications: list = field(default_factory=list)
    projects: list = field(default_factory=list)
    links: list = field(default_factory=list)
    raw_text: str = ""
    years_experience: Optional[float] = None
    education_level: Optional[str] = None
    current_role: Optional[str] = None
    current_company: Optional[str] = None


class ResumeParser:
    """
    Parses resumes into structured data.

    Two-phase pipeline:
      Phase 1 (instant): Extract text + regex for name/email/phone/location
      Phase 2 (background): LLM for full structure (experience, skills, education, etc.)
    """

    # ── Phase 1: Quick extract (inline during upload, <500ms) ─────────

    async def extract_text(self, file_content: bytes, filename: str) -> str:
        """Extract raw text from resume file. Fast, no LLM."""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext == "pdf":
            return await self._extract_text_pdf(file_content)
        elif ext == "docx":
            return await self._extract_text_docx(file_content)
        elif ext == "txt":
            return file_content.decode("utf-8", errors="replace")
        else:
            raise ValueError(f"Unsupported file format: {ext}")

    def quick_parse(self, raw_text: str, filename: str = "") -> "ParsedResume":
        """
        Instant regex/heuristic extraction — no LLM call.
        Extracts: name, email, phone, location, links.
        Returns a ParsedResume with basic fields populated.
        """
        import time as _time
        t0 = _time.perf_counter()

        lines = [l.strip() for l in raw_text.split("\n") if l.strip()]

        # ── Email ──
        email_match = _EMAIL_RE.search(raw_text)
        email = email_match.group(0) if email_match else None

        # ── Phone ──
        phone = None
        for m in _PHONE_RE.finditer(raw_text[:2000]):  # phones usually near top
            candidate_phone = m.group(0).strip()
            digits = re.sub(r'\D', '', candidate_phone)
            if 7 <= len(digits) <= 15:
                phone = candidate_phone
                break

        # ── Name: first non-empty line that isn't an email/phone/url ──
        name = ""
        for line in lines[:5]:
            line_lower = line.lower()
            if "@" in line or "http" in line_lower or "linkedin" in line_lower:
                continue
            if re.match(r'^[\d\+\(\)]+', line):  # starts with phone-like chars
                continue
            if line_lower.startswith(("resume", "curriculum", "cv ", "objective")):
                continue
            # Likely the name — take it
            # Clean: remove trailing pipe/dash suffixed info
            name = re.split(r'\s*[|•]\s*', line)[0].strip()
            # Cap at ~50 chars (avoid long header lines)
            if len(name) <= 50 and len(name.split()) <= 6:
                break
            name = ""
        if not name:
            # Fallback to filename
            name = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()

        # ── Location (heuristic: near top, after name/contact) ──
        location = None
        loc_match = _LOCATION_RE.search(raw_text[:1500])
        if loc_match:
            location = loc_match.group(1).strip()

        # ── Links ──
        links = []
        linkedin = _LINKEDIN_RE.search(raw_text)
        if linkedin:
            links.append({"url": "https://" + linkedin.group(0), "label": "LinkedIn"})
        for url_match in _URL_RE.finditer(raw_text[:3000]):
            url = url_match.group(0)
            if "linkedin" not in url.lower():
                links.append({"url": url, "label": "Website"})
            if len(links) >= 5:
                break

        t1 = _time.perf_counter()
        logger.info(f"[TIMING] {filename}: quick_parse {(t1-t0)*1000:.0f}ms -> name={name}, email={email}")

        return ParsedResume(
            name=name or "Unknown",
            email=email,
            phone=phone,
            location=location,
            links=links,
            raw_text=raw_text,
        )

    # ── Phase 2: Full LLM parse (runs in background) ─────────────────

    async def parse(self, file_content: bytes, filename: str) -> "ParsedResume":
        """Full LLM-powered parse. Call from background task."""
        import time as _time

        # Step 1: Extract raw text
        t0 = _time.perf_counter()
        raw_text = await self.extract_text(file_content, filename)

        if not raw_text.strip():
            raise ValueError("Could not extract any text from the resume file")

        t1 = _time.perf_counter()
        logger.info(f"[TIMING] {filename}: text extraction {(t1-t0)*1000:.0f}ms ({len(raw_text)} chars)")

        # Step 2: LLM structure extraction
        structured = await self._llm_extract_structure(raw_text)
        t2 = _time.perf_counter()
        logger.info(f"[TIMING] {filename}: LLM parse {(t2-t1)*1000:.0f}ms")

        # Step 3: Build ParsedResume
        raw_email = structured.get("email")
        if raw_email and ("@" not in str(raw_email) or raw_email.lower() in ("email", "n/a", "null", "none")):
            raw_email = None

        parsed = ParsedResume(
            name=structured.get("name", "Unknown"),
            email=raw_email,
            phone=structured.get("phone"),
            location=structured.get("location"),
            summary=structured.get("summary"),
            experience=structured.get("experience", []),
            education=structured.get("education", []),
            skills_mentioned=structured.get("skills_mentioned", []),
            certifications=structured.get("certifications", []),
            projects=structured.get("projects", []),
            links=structured.get("links", []),
            raw_text=raw_text,
            years_experience=structured.get("years_experience"),
            education_level=structured.get("education_level"),
            current_role=structured.get("current_role"),
            current_company=structured.get("current_company"),
        )

        logger.info(
            f"Parsed resume: {parsed.name}, "
            f"{len(parsed.skills_mentioned)} skills, "
            f"{len(parsed.experience)} experiences"
        )
        return parsed

    async def _extract_text_pdf(self, content: bytes) -> str:
        """Extract text from PDF bytes using pypdf (offloaded to thread)."""
        return await asyncio.to_thread(self._extract_text_pdf_sync, content)

    def _extract_text_pdf_sync(self, content: bytes) -> str:
        """Synchronous PDF text extraction."""
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)

    async def _extract_text_docx(self, content: bytes) -> str:
        """Extract text from DOCX bytes using python-docx (offloaded to thread)."""
        return await asyncio.to_thread(self._extract_text_docx_sync, content)

    def _extract_text_docx_sync(self, content: bytes) -> str:
        """Synchronous DOCX text extraction."""
        from docx import Document

        doc = Document(io.BytesIO(content))
        paragraphs = []
        for para in doc.paragraphs:
            if para.text.strip():
                paragraphs.append(para.text)
        return "\n".join(paragraphs)

    async def _llm_extract_structure(self, raw_text: str) -> dict:
        """Use LLM to extract structured resume data from raw text."""
        max_chars = 12000
        if len(raw_text) > max_chars:
            raw_text = raw_text[:max_chars] + "\n\n[Resume truncated for processing]"

        result = await llm_client.complete_json(
            system_prompt=RESUME_EXTRACTION_PROMPT,
            user_message=f"Parse this resume:\n\n{raw_text}",
            max_tokens=3000,
        )
        return result

    def to_dict(self, parsed: ParsedResume) -> dict:
        """Convert ParsedResume to a dictionary (for JSON storage)."""
        d = asdict(parsed)
        d.pop("raw_text", None)  # Don't include raw text in the parsed JSON
        return d


# Singleton
resume_parser = ResumeParser()
