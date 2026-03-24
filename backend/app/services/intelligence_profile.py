"""
Candidate Intelligence Profile Generator.

Takes parsed resume data and generates a rich, AI-written candidate
intelligence brief — narrative assessments, not just structured data.
This runs once at upload time and is stored alongside the parsed resume.
"""

import json
import logging
from typing import Optional
from dataclasses import asdict

from app.utils.llm_client import llm_client
from app.services.resume_parser import ParsedResume

logger = logging.getLogger(__name__)

INTELLIGENCE_PROMPT = """You are a senior talent intelligence analyst at a top-tier recruiting firm.
Given a candidate's parsed resume data, produce a rich intelligence profile that helps recruiters
understand this person quickly and thoroughly. Write in a professional, confident editorial tone.

Return a JSON object with EXACTLY these keys:

- "executive_summary": 3-5 sentence written assessment of who this candidate is, their trajectory, and what makes them notable. Synthesize and provide insight — don't just restate the resume.

- "seniority_level": One of: Executive, Staff/Principal, Senior, Mid-Level, Early Career, Intern/Student

- "career_narrative": 2-3 sentence narrative about their career arc. What story does their trajectory tell? Builder, climber, specialist, generalist? Industry pivots? Growth patterns?

- "strengths": [3-5 specific, evidence-based observations referencing actual experience or patterns. 1-2 sentences each.]

- "considerations": [Honest observations — short tenures, gaps, narrow focus, missing credentials. Empty array if nothing notable. IMPORTANT: Do NOT flag "limited education" or "no formal degree" if the candidate has professional certifications (ACA, CPA, CFA, ACCA, PMP, CISSP, etc.) — these ARE formal credentials and often more relevant than degrees for professional roles.]

- "skill_narrative": 2-3 sentences characterizing their skill profile. Don't list skills — describe what kind of professional they are.

- "skill_categories": {"category_name": ["skill1", "skill2"]} — group ALL skills into meaningful categories (Languages, Frameworks, Cloud/DevOps, Databases, Data/ML, Design, Soft Skills, Tools, etc.)

- "culture_signals": 1-2 sentences on work style, values, or culture fit inferred from resume signals.

- "ideal_roles": [2-4 specific role types/environments where this candidate would thrive. Be specific — not just "Software Engineer" but "Backend Engineer at a growth-stage startup".]

- "ideal_roles_narrative": 2-3 sentence prose paragraph on what types of roles and environments suit them and why, connected to their experience.

- "career_timeline_briefs": [{"company": "Company Name", "title": "Job Title", "brief": "1-2 sentence analyst-written summary of what they did and achieved in this role. Focus on impact, scope, and what it tells us."}] — write one for EACH role in the resume. CRITICAL: include the exact job title so each role can be uniquely identified, especially when the candidate held multiple roles at the same company. CRITICAL ANTI-REPETITION RULE: Each brief MUST be substantively different from every other brief. Do NOT use the same sentence structure, opening word, or pattern across briefs. Vary your framing: some briefs should lead with the achievement, some with scope, some with the strategic context, some with a transition story. If a candidate held 5 roles, a reader should be able to tell IMMEDIATELY which brief belongs to which role without looking at the company name.

- "talking_points": [3-5 specific things a recruiter should ask about or discuss. Not generic questions — genuinely interesting or clarifying.]

Rules:
- Write for a recruiter audience — practical, direct, insightful
- Ground every claim in the actual resume data
- Be opinionated — recruiters want assessments, not summaries
- If the resume is thin, say so honestly
- Certifications (ACA, CPA, CFA, ACCA, PMP, CISSP, FRM, etc.) count as formal credentials. Do NOT contradict yourself by saying "limited education background" on the same brief where you list these certifications. If degree info is sparse but certifications are present, note the strong professional credentials instead.
- For senior professionals (15+ years experience), career trajectory and certifications often matter more than formal degrees. Frame education observations accordingly.
- DOMAIN AWARENESS: Adapt your language and framing to the candidate's domain. For finance professionals, highlight regulatory knowledge, controllership scope, audit exposure, and P&L responsibility. For technology professionals, highlight architecture decisions, system scale, and tech stack depth. For consulting professionals, highlight client engagement scope, methodology expertise, and delivery track record. For operations professionals, highlight process improvements, efficiency gains, and operational scope. Do NOT use generic tech startup language for a 20-year finance veteran, and vice versa.
- CAREER TIMELINE DIFFERENTIATION: When writing career_timeline_briefs, focus on what CHANGED between each role. What new responsibility appeared? What scope expanded? What industry shifted? What skill deepened? The briefs should tell a STORY of progression, not repeat the same template with different company names."""


async def generate_intelligence_profile(parsed: ParsedResume) -> Optional[dict]:
    """
    Generate an AI-powered intelligence profile from a parsed resume.
    Returns a dict ready to be stored as JSONB, or None if generation fails.
    """
    try:
        # Build a compact representation for the LLM (exclude raw_text, minimize whitespace)
        resume_data = asdict(parsed)
        resume_data.pop("raw_text", None)

        result = await llm_client.complete_json(
            system_prompt=INTELLIGENCE_PROMPT,
            user_message=f"Generate an intelligence profile for this candidate:\n\n{json.dumps(resume_data, indent=2, default=str)}",
            max_tokens=3000,
        )

        # Validate we got the key fields
        if not result.get("executive_summary"):
            logger.warning("Intelligence profile missing executive_summary, skipping")
            return None

        logger.info(f"Generated intelligence profile for {parsed.name}")
        return result

    except Exception as e:
        logger.error(f"Intelligence profile generation failed for {parsed.name}: {e}")
        # Non-fatal — candidate still gets created, just without the intelligence layer
        return None
