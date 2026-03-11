"""
Skill Assessment Pipeline — The core intelligence engine of VetLayer.

Speed-optimized single-call architecture:
  - Only assesses job-relevant skills (6-12 skills, not all 30+)
  - Minimal output format (no evidence quotes — just depth + reasoning)
  - Deterministic evidence extraction post-LLM (no extra API calls)
  - Pipeline timing logs for every stage
  - Result caching to skip repeat analyses
  - Target: <10 seconds per analysis
"""

import re
import copy
import time
import hashlib
import logging
from collections import OrderedDict
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

from app.utils.llm_client import llm_client

logger = logging.getLogger(__name__)

# Pipeline version — bump this when prompt, scoring, or output format changes
# so the cache doesn't return stale results after algorithm updates.
PIPELINE_VERSION = "v0.7"


# ═══════════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Evidence:
    """A piece of evidence supporting a skill claim."""
    evidence_type: str
    description: str
    source_text: str
    strength: float = 0.5

@dataclass
class SkillAssessment:
    """Complete assessment of a single skill."""
    name: str
    category: str
    estimated_depth: int         # 0-5 (0 = not found)
    depth_confidence: float      # 0.0 - 1.0
    depth_reasoning: str
    evidence: List[Evidence] = field(default_factory=list)
    last_used_year: Optional[int] = None
    years_of_use: Optional[float] = None

@dataclass
class PipelineTimings:
    """Timing breakdown for each pipeline stage."""
    resume_format_ms: float = 0
    llm_call_ms: float = 0
    result_parse_ms: float = 0
    evidence_extraction_ms: float = 0
    total_ms: float = 0
    cache_hit: bool = False


# ═══════════════════════════════════════════════════════════════════════
# System Prompt — minimal output, maximum speed
# ═══════════════════════════════════════════════════════════════════════

FAST_ASSESSMENT_PROMPT = """You are VetLayer's Skill Assessment Engine. Expert technical recruiter with 15+ years of experience evaluating software engineers.

Given JOB SKILLS and a CANDIDATE RESUME, rate each skill's depth on this scale:

DEPTH SCALE WITH BEHAVIORAL ANCHORS:
0 = NOT FOUND: Skill not mentioned or evidenced anywhere on the resume.
1 = AWARENESS: Skill listed in a skills section or mentioned in passing, but no concrete usage described. Example: "Familiar with Docker" with no project using it.
2 = BEGINNER: Used in coursework, tutorials, personal side projects, or briefly in a professional setting. Example: "Completed a React tutorial", "Used Redis in a hackathon."
3 = INTERMEDIATE/PROFESSIONAL: Used in production work with real users. Built or maintained features using this skill. 1+ years of regular hands on use. Example: "Built REST APIs with FastAPI serving production traffic", "Developed React components used by 10K+ users."
4 = ADVANCED: Led architecture decisions, designed systems, optimized performance, or mentored others in this skill. 2+ years of deep professional use. Example: "Architected microservices migration serving 2M users", "Led team adoption of Kubernetes, designed deployment pipelines."
5 = EXPERT: Industry recognized, published research, created widely adopted tools/libraries, or deep specialist. Conference speaker, open source maintainer, or core contributor. Example: "Created open source library with 5K+ stars", "Published paper on distributed consensus."

CRITICAL — IMPLIED SKILL RULES (you MUST follow these):
When a candidate has professional experience building production applications with a FRAMEWORK, they NECESSARILY have professional level skill in that framework's FOUNDATION technologies. This is not optional, it is logically required.

Specifically:
- React, Next.js, Vue, Angular, Svelte experience at depth 3+ implies HTML depth MUST be >=3, CSS depth MUST be >=3, JavaScript depth MUST be >=3, Browser APIs depth MUST be >=2
- Node.js, Express, Nest.js experience at depth 3+ implies JavaScript depth MUST be >=3
- Django, Flask, FastAPI experience at depth 3+ implies Python depth MUST be >=3
- Spring Boot experience at depth 3+ implies Java depth MUST be >=3
- Rails experience at depth 3+ implies Ruby depth MUST be >=3
- Any production web application work implies the primary language depth MUST be >=3
- Building web apps with caching, localStorage, sessionStorage, fetch, WebSockets, service workers, or any client side storage/networking implies Browser APIs depth MUST be >=2

Example: A developer who built production React apps professionally CANNOT have HTML at depth 2. React IS HTML+CSS+JS. Rate HTML >=3, CSS >=3, JS >=3, Browser APIs >=2.

IMPORTANT: Do NOT require the skill to be explicitly listed by name. If the resume shows production React work, that IS evidence of HTML/CSS/JS/Browser APIs proficiency. If the resume mentions "caching layer", "localStorage", "real time notifications", "WebSocket", that IS Browser APIs evidence.

Return compact JSON:
{"a":[{"n":"React","d":3,"c":0.8,"r":"Built 1600 line production React app at MOVZZ.","y":2024},{"n":"SASS","d":0,"c":0,"r":"Not found on resume.","y":null}]}

Fields: n=name(exact as requested), d=depth(0 to 5), c=confidence(0 to 1), r=reasoning(1 short sentence, plain language, NO dashes or special punctuation), y=last_used_year(integer or null)
EVERY requested skill MUST appear in the output. Not found=d:0,c:0. Listed only=d:1,c:0.2.

IMPORTANT: Only assess TECHNICAL skills (languages, frameworks, tools, platforms). If a non-technical/soft skill
slips into the requested list (e.g. "communication", "teamwork", "leadership"), rate it d:0, c:0 with reasoning
"Non-technical skill, not assessed." Do NOT try to infer soft skill depth from resume content.

IMPORTANT: In your reasoning text, never use dashes, emdashes, or endashes. Use commas or periods instead."""


# ═══════════════════════════════════════════════════════════════════════
# Evidence Extractor — deterministic, no LLM calls
# ═══════════════════════════════════════════════════════════════════════

# Skill name variants for evidence matching
_EVIDENCE_ALIASES = {
    "html": ["html", "html5", "html 5"],
    "css": ["css", "css3", "css 3", "stylesheets", "stylesheet", "tailwind", "bootstrap"],
    "sass/scss": ["sass", "scss", "sass/scss", "less"],
    "javascript": ["javascript", "js", "ecmascript", "es6", "es2015"],
    "typescript": ["typescript", "ts"],
    "react": ["react", "react.js", "reactjs"],
    "vue": ["vue", "vue.js", "vuejs"],
    "angular": ["angular", "angular.js", "angularjs"],
    "next.js": ["next.js", "nextjs", "next"],
    "node.js": ["node.js", "nodejs", "node"],
    "python": ["python", "py"],
    "java": ["java", "jdk"],
    "go": ["golang", "go lang", "go programming"],
    "fastapi": ["fastapi", "fast api"],
    "django": ["django", "drf", "django rest"],
    "flask": ["flask"],
    "express": ["express", "express.js", "expressjs"],
    "postgresql": ["postgresql", "postgres", "psql"],
    "mongodb": ["mongodb", "mongo"],
    "mysql": ["mysql", "mariadb"],
    "redis": ["redis"],
    "kafka": ["kafka", "apache kafka"],
    "docker": ["docker", "containerization", "container"],
    "kubernetes": ["kubernetes", "k8s"],
    "aws": ["aws", "amazon web services", "ecs", "rds", "s3", "lambda"],
    "gcp": ["gcp", "google cloud"],
    "azure": ["azure", "microsoft azure"],
    "graphql": ["graphql", "graph ql"],
    "rest api": ["rest api", "restful", "rest apis", "rest"],
    "git": ["git", "github", "gitlab", "version control"],
    "webpack": ["webpack", "vite", "rollup", "esbuild", "bundler"],
    "ci/cd": ["ci/cd", "ci cd", "cicd", "continuous integration", "github actions", "jenkins"],
    "microservices": ["microservices", "micro services", "microservice"],
    "agile": ["agile", "scrum", "kanban", "sprint"],
    "browser apis": [
        "browser api", "browser apis", "web api", "web apis",
        "web storage", "web storage api",
        "caching", "cache api", "browser caching",
        "local storage", "localstorage", "localStorage",
        "session storage", "sessionstorage", "sessionStorage",
        "indexeddb", "indexed db",
        "service worker", "service workers",
        "web workers", "web worker",
        "fetch api", "fetch(", "window.fetch",
        "xmlhttprequest", "xhr",
        "dom manipulation", "dom api", "document.querySelector",
        "web components", "shadow dom", "custom elements",
        "intersection observer", "mutation observer", "resize observer",
        "websocket", "websockets", "WebSocket",
        "canvas", "webgl",
        "geolocation", "notification api", "notifications",
        "clipboard api", "drag and drop",
        "file api", "FileReader",
        "history api", "pushState", "popstate",
        "broadcast channel", "postMessage",
        "performance api", "requestAnimationFrame",
    ],
}


def _get_skill_variants(skill_name: str) -> List[str]:
    """Get all text variants of a skill name for searching."""
    name_lower = skill_name.lower().strip()
    # Check alias map
    for canonical, variants in _EVIDENCE_ALIASES.items():
        if name_lower in variants or name_lower == canonical:
            return variants
    # Fallback: just the name itself and common suffixes
    variants = [name_lower]
    if "." in name_lower:
        variants.append(name_lower.replace(".", ""))  # "node.js" → "nodejs"
    if " " in name_lower:
        variants.append(name_lower.replace(" ", ""))  # "vue js" → "vuejs"
    return variants


def extract_evidence(skill_name: str, parsed_resume: dict) -> List[Evidence]:
    """
    Deterministic evidence extraction — searches resume text for lines
    mentioning a skill. No LLM call needed.

    Returns a list of Evidence objects with source_text snippets.
    """
    evidence_list = []
    variants = _get_skill_variants(skill_name)

    # Build a regex pattern that matches any variant (word boundary)
    escaped = [re.escape(v) for v in variants]
    pattern = re.compile(r'\b(' + '|'.join(escaped) + r')\b', re.IGNORECASE)

    # Search in experience descriptions
    for exp in parsed_resume.get("experience", []):
        desc = exp.get("description", "")
        techs = exp.get("technologies", [])
        title = exp.get("title", "")
        company = exp.get("company", "")

        # Check in description text
        if desc and pattern.search(desc):
            # Extract the relevant sentence
            snippet = _extract_snippet(desc, pattern)
            evidence_list.append(Evidence(
                evidence_type="experience",
                description=f"Used in role: {title} @ {company}",
                source_text=snippet,
                strength=0.9,
            ))

        # Check in technologies list
        tech_str = ", ".join(techs)
        if tech_str and pattern.search(tech_str):
            evidence_list.append(Evidence(
                evidence_type="technology_used",
                description=f"Listed as technology at {company}",
                source_text=f"Technologies: {tech_str}",
                strength=0.7,
            ))

    # Search in projects
    for proj in parsed_resume.get("projects", []):
        proj_name = proj.get("name", "")
        proj_desc = proj.get("description", "")
        proj_techs = proj.get("technologies", [])

        if proj_desc and pattern.search(proj_desc):
            snippet = _extract_snippet(proj_desc, pattern)
            evidence_list.append(Evidence(
                evidence_type="project",
                description=f"Used in project: {proj_name}",
                source_text=snippet,
                strength=0.8,
            ))

        tech_str = ", ".join(proj_techs)
        if tech_str and pattern.search(tech_str):
            evidence_list.append(Evidence(
                evidence_type="project_technology",
                description=f"Listed in project: {proj_name}",
                source_text=f"Tech: {tech_str}",
                strength=0.6,
            ))

    # Check in skills_mentioned list
    for skill in parsed_resume.get("skills_mentioned", []):
        if pattern.search(skill):
            evidence_list.append(Evidence(
                evidence_type="skills_list",
                description=f"Listed in skills section",
                source_text=skill,
                strength=0.4,
            ))
            break  # Only one entry from skills list

    # Check in summary
    summary = parsed_resume.get("summary", "")
    if summary and pattern.search(summary):
        snippet = _extract_snippet(summary, pattern)
        evidence_list.append(Evidence(
            evidence_type="summary",
            description="Mentioned in professional summary",
            source_text=snippet,
            strength=0.5,
        ))

    # Deduplicate by source_text
    seen = set()
    unique_evidence = []
    for ev in evidence_list:
        key = ev.source_text[:80]
        if key not in seen:
            seen.add(key)
            unique_evidence.append(ev)

    return unique_evidence


def _extract_snippet(text: str, pattern: re.Pattern, context_chars: int = 120) -> str:
    """Extract a relevant snippet around the first match of the pattern."""
    match = pattern.search(text)
    if not match:
        return text[:150]

    start = max(0, match.start() - context_chars // 2)
    end = min(len(text), match.end() + context_chars // 2)

    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."

    return snippet


# ═══════════════════════════════════════════════════════════════════════
# Result Cache — skip repeat analyses
# ═══════════════════════════════════════════════════════════════════════

class PipelineCache:
    """In-memory LRU cache for pipeline results. Keyed by hash(resume + skills)."""

    def __init__(self, max_size: int = 200):
        self._cache: OrderedDict[str, List[SkillAssessment]] = OrderedDict()
        self._max_size = max_size

    def _make_key(self, resume_text: str, skill_list: str) -> str:
        raw = f"{PIPELINE_VERSION}|||{resume_text}|||{skill_list}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, resume_text: str, skill_list: str) -> Optional[List[SkillAssessment]]:
        key = self._make_key(resume_text, skill_list)
        result = self._cache.get(key)
        if result is not None:
            self._cache.move_to_end(key)
            logger.info(f"Cache HIT: {key}")
            return [copy.deepcopy(a) for a in result]
        return None

    def put(self, resume_text: str, skill_list: str, assessments: List[SkillAssessment]):
        key = self._make_key(resume_text, skill_list)
        # Evict least recently used if full
        if len(self._cache) >= self._max_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        self._cache[key] = [copy.deepcopy(a) for a in assessments]
        logger.info(f"Cache PUT: {key} ({len(assessments)} assessments)")

    def clear(self):
        self._cache.clear()
        logger.info("Cache cleared")

    @property
    def size(self) -> int:
        return len(self._cache)


# Global cache instance
_pipeline_cache = PipelineCache()


# ═══════════════════════════════════════════════════════════════════════
# Pipeline
# ═══════════════════════════════════════════════════════════════════════

class SkillPipeline:
    """
    Speed-optimized single-call pipeline with:
    - Deterministic evidence extraction (no extra LLM calls)
    - Stage-by-stage timing logs
    - Result caching
    Target: <10 seconds.
    """

    async def run(
        self,
        parsed_resume: dict,
        required_skills: list = None,
        preferred_skills: list = None,
    ) -> tuple:
        """
        Run job-focused skill assessment. Always 1 LLM call, minimal output.
        Returns: (assessments: List[SkillAssessment], timings: PipelineTimings)
        """
        timings = PipelineTimings()
        pipeline_start = time.time()

        # ── Stage 1: Format resume ────────────────────────────────────
        t0 = time.time()
        resume_text = self._format_resume_compact(parsed_resume)
        timings.resume_format_ms = (time.time() - t0) * 1000

        # ── Build skill list ──────────────────────────────────────────
        skill_names = []
        skill_metadata = {}  # name -> {"mode": "req"/"pref", "min_depth": int}
        if required_skills:
            for s in required_skills:
                name = s.get('skill', '')
                skill_names.append(name)
                skill_metadata[name] = {"mode": "req", "min_depth": s.get('min_depth', 2)}
        if preferred_skills:
            for s in preferred_skills:
                name = s.get('skill', '')
                skill_names.append(name)
                skill_metadata[name] = {"mode": "pref", "min_depth": 0}

        if not skill_names:
            logger.warning("No job skills to assess")
            return [], timings

        skill_list_text = ", ".join(skill_names)

        # ── Check cache ───────────────────────────────────────────────
        cached = _pipeline_cache.get(resume_text, skill_list_text)
        if cached is not None:
            timings.cache_hit = True
            timings.total_ms = (time.time() - pipeline_start) * 1000
            logger.info(f"⚡ Cache hit — returning {len(cached)} cached assessments in {timings.total_ms:.0f}ms")
            self._log_timings(timings, len(cached))
            return cached, timings

        logger.info(f"Fast assessment: {len(skill_names)} skills → 1 LLM call")

        # ── Stage 2: LLM call ─────────────────────────────────────────
        t0 = time.time()
        result = await llm_client.complete_json(
            system_prompt=FAST_ASSESSMENT_PROMPT,
            user_message=f"Skills: {skill_list_text}\n\nResume:\n{resume_text}",
            max_tokens=2000,
        )
        timings.llm_call_ms = (time.time() - t0) * 1000

        # ── Stage 3: Parse results ────────────────────────────────────
        t0 = time.time()
        assessments = []
        for item in result.get("a", result.get("assessments", [])):
            name = item.get("n", item.get("name", "Unknown"))
            depth = item.get("d", item.get("estimated_depth", 0))
            confidence = item.get("c", item.get("depth_confidence", 0.0))
            reasoning = item.get("r", item.get("depth_reasoning", ""))
            # Sanitize LLM reasoning: strip dashes/emdashes from output
            reasoning = reasoning.replace("—", ", ").replace("–", ", ").replace(" - ", ", ")

            # Parse last_used_year from LLM response
            last_year = item.get("y", item.get("last_used_year"))
            if last_year and isinstance(last_year, (int, float)) and last_year > 2000:
                last_year = int(last_year)
            else:
                last_year = None

            assessments.append(SkillAssessment(
                name=name,
                category=item.get("cat", item.get("category", "unknown")),
                estimated_depth=max(0, min(5, depth)),
                depth_confidence=max(0.0, min(1.0, confidence)),
                depth_reasoning=reasoning,
                evidence=[],
                last_used_year=last_year,
            ))
        timings.result_parse_ms = (time.time() - t0) * 1000

        # ── Stage 4: Deterministic evidence extraction ────────────────
        t0 = time.time()
        for assessment in assessments:
            if assessment.estimated_depth > 0:
                assessment.evidence = extract_evidence(assessment.name, parsed_resume)
                # Boost confidence based on evidence count
                if assessment.evidence:
                    ev_count = len(assessment.evidence)
                    # More evidence = higher confidence (but don't exceed 1.0)
                    evidence_boost = min(ev_count * 0.05, 0.15)
                    assessment.depth_confidence = min(1.0, assessment.depth_confidence + evidence_boost)
        timings.evidence_extraction_ms = (time.time() - t0) * 1000

        # ── Cache results ─────────────────────────────────────────────
        _pipeline_cache.put(resume_text, skill_list_text, assessments)

        timings.total_ms = (time.time() - pipeline_start) * 1000
        self._log_timings(timings, len(assessments))

        return assessments, timings

    def _log_timings(self, timings: PipelineTimings, skill_count: int):
        """Log a clean timing breakdown for monitoring."""
        logger.info(
            f"\n{'═' * 50}\n"
            f"  PIPELINE TIMING BREAKDOWN\n"
            f"{'─' * 50}\n"
            f"  resume_format:       {timings.resume_format_ms:>7.1f}ms\n"
            f"  llm_call:            {timings.llm_call_ms:>7.1f}ms\n"
            f"  result_parse:        {timings.result_parse_ms:>7.1f}ms\n"
            f"  evidence_extraction: {timings.evidence_extraction_ms:>7.1f}ms\n"
            f"{'─' * 50}\n"
            f"  TOTAL:               {timings.total_ms:>7.1f}ms  "
            f"({'CACHE HIT' if timings.cache_hit else f'{skill_count} skills assessed'})\n"
            f"{'═' * 50}"
        )

    def _format_resume_compact(self, parsed_resume: dict) -> str:
        """Format resume compactly to minimize input tokens."""
        sections = []

        if parsed_resume.get("name"):
            sections.append(f"NAME: {parsed_resume['name']}")

        if parsed_resume.get("summary"):
            summary = parsed_resume['summary'][:300]
            sections.append(f"SUMMARY: {summary}")

        if parsed_resume.get("experience"):
            exp_lines = ["EXPERIENCE:"]
            for exp in parsed_resume["experience"]:
                title = exp.get("title", "")
                company = exp.get("company", "")
                dates = f"{exp.get('start_date', '?')} to {exp.get('end_date', '?')}"
                desc = exp.get("description", "")[:200]
                techs = ", ".join(exp.get("technologies", []))
                exp_lines.append(f"  {title} @ {company} ({dates})")
                if desc:
                    exp_lines.append(f"  {desc}")
                if techs:
                    exp_lines.append(f"  Tech: {techs}")
            sections.append("\n".join(exp_lines))

        if parsed_resume.get("skills_mentioned"):
            sections.append(f"SKILLS: {', '.join(parsed_resume['skills_mentioned'])}")

        if parsed_resume.get("projects"):
            proj_lines = ["PROJECTS:"]
            for proj in parsed_resume["projects"]:
                name = proj.get("name", "")
                desc = proj.get("description", "")[:150]
                techs = ", ".join(proj.get("technologies", []))
                proj_lines.append(f"  {name}: {desc}")
                if techs:
                    proj_lines.append(f"  Tech: {techs}")
            sections.append("\n".join(proj_lines))

        if parsed_resume.get("education"):
            edu = parsed_resume["education"][0] if parsed_resume["education"] else {}
            if edu:
                sections.append(f"EDUCATION: {edu.get('degree', '')} {edu.get('field', '')} @ {edu.get('institution', '')}")

        return "\n\n".join(sections)


def assessment_to_dict(assessment: SkillAssessment) -> dict:
    """Convert a SkillAssessment to a serializable dict."""
    return {
        "name": assessment.name,
        "category": assessment.category,
        "estimated_depth": assessment.estimated_depth,
        "depth_confidence": assessment.depth_confidence,
        "depth_reasoning": assessment.depth_reasoning,
        "evidence": [
            {
                "evidence_type": e.evidence_type,
                "description": e.description,
                "source_text": e.source_text,
                "strength": e.strength,
            }
            for e in assessment.evidence
        ],
        "last_used_year": assessment.last_used_year,
        "years_of_use": assessment.years_of_use,
    }


def timings_to_dict(timings: PipelineTimings) -> dict:
    """Convert PipelineTimings to a serializable dict."""
    return {
        "pipeline_version": PIPELINE_VERSION,
        "resume_format_ms": round(timings.resume_format_ms, 1),
        "llm_call_ms": round(timings.llm_call_ms, 1),
        "result_parse_ms": round(timings.result_parse_ms, 1),
        "evidence_extraction_ms": round(timings.evidence_extraction_ms, 1),
        "total_ms": round(timings.total_ms, 1),
        "cache_hit": timings.cache_hit,
    }


# Singleton
skill_pipeline = SkillPipeline()
