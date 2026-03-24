"""
Dynamic Taxonomy Generator — Auto-generates skill definitions for unknown domains.

When VetLayer encounters skills outside its built-in taxonomy (450+ tech skills),
this module uses the LLM to generate:
  - Skill depth definitions (what does depth 1-5 look like for "media buying"?)
  - Alias variants (e.g., "media buying" → "paid media", "ad buying", "programmatic")
  - Related skills for transferability
  - Evidence keywords to look for in resumes

Results are cached per skill so the LLM is only called once per unknown skill.
"""

import logging
from collections import OrderedDict
from typing import Dict, Any, List, Optional

from app.utils.llm_client import llm_client

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Cache for generated taxonomies
# ═══════════════════════════════════════════════════════════════════════

class TaxonomyCache:
    """In-memory cache for dynamically generated skill taxonomies."""

    def __init__(self, max_size: int = 500):
        self._cache: OrderedDict[str, Dict] = OrderedDict()
        self._max_size = max_size

    def get(self, skill_name: str) -> Optional[Dict]:
        key = _normalize(skill_name)
        result = self._cache.get(key)
        if result:
            self._cache.move_to_end(key)
        return result

    def put(self, skill_name: str, taxonomy: Dict):
        key = _normalize(skill_name)
        if len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)
        self._cache[key] = taxonomy

    def has(self, skill_name: str) -> bool:
        return _normalize(skill_name) in self._cache

    @property
    def size(self) -> int:
        return len(self._cache)


_taxonomy_cache = TaxonomyCache()


def _normalize(skill_name: str) -> str:
    return skill_name.lower().strip()


# ═══════════════════════════════════════════════════════════════════════
# Dynamic taxonomy generation prompt
# ═══════════════════════════════════════════════════════════════════════

_TAXONOMY_GENERATION_PROMPT = """You are a skill taxonomy expert. Given a professional skill name and optional job context, generate a structured taxonomy entry.

Return JSON with these fields:
{
  "skill": "exact skill name",
  "category": "one of: marketing, finance, hr, legal, healthcare, supply_chain, sales, operations, consulting, real_estate, media, education, general_business",
  "aliases": ["list", "of", "alternate", "names", "or", "abbreviations"],
  "depth_definitions": {
    "1": "What depth 1 (awareness) looks like for this skill",
    "2": "What depth 2 (beginner) looks like",
    "3": "What depth 3 (intermediate/professional) looks like",
    "4": "What depth 4 (advanced) looks like",
    "5": "What depth 5 (expert) looks like"
  },
  "evidence_keywords": ["keywords", "to", "look", "for", "in", "resumes"],
  "related_skills": ["skills", "that", "transfer", "to", "this"],
  "transferability": [
    {"skill": "related_skill_name", "coefficient": 0.5}
  ]
}

Be specific and practical. Depth definitions should reference concrete, observable resume evidence.
Evidence keywords should be terms you would actually see on resumes.
Related skills should be skills where experience in one genuinely helps with the other."""


async def generate_skill_taxonomy(
    skill_name: str,
    job_title: str = "",
    job_description: str = "",
) -> Dict[str, Any]:
    """
    Generate a taxonomy entry for an unknown skill using the LLM.

    Returns a structured taxonomy dict with aliases, depth definitions,
    evidence keywords, and transferability mappings.

    Results are cached so identical skills aren't regenerated.
    """
    # Check cache first
    cached = _taxonomy_cache.get(skill_name)
    if cached:
        logger.info(f"Dynamic taxonomy cache hit: {skill_name}")
        return cached

    # Build context
    context = f"Skill: {skill_name}"
    if job_title:
        context += f"\nJob Title: {job_title}"
    if job_description:
        # Only use first 500 chars of description for context
        context += f"\nJob Context: {job_description[:500]}"

    try:
        result = await llm_client.complete_json(
            system_prompt=_TAXONOMY_GENERATION_PROMPT,
            user_message=context,
            max_tokens=800,
        )

        # Validate and normalize the result
        taxonomy = _validate_taxonomy(result, skill_name)

        # Cache it
        _taxonomy_cache.put(skill_name, taxonomy)

        logger.info(
            f"Generated dynamic taxonomy for '{skill_name}': "
            f"category={taxonomy['category']}, "
            f"aliases={len(taxonomy['aliases'])}, "
            f"evidence_keywords={len(taxonomy['evidence_keywords'])}"
        )

        return taxonomy

    except Exception as e:
        logger.error(f"Failed to generate taxonomy for '{skill_name}': {e}")
        # Return a minimal fallback taxonomy
        return _fallback_taxonomy(skill_name)


async def generate_batch_taxonomies(
    skill_names: List[str],
    job_title: str = "",
    job_description: str = "",
) -> Dict[str, Dict]:
    """Generate taxonomies for multiple unknown skills in a single LLM call."""

    # Filter out already-cached skills
    uncached = [s for s in skill_names if not _taxonomy_cache.has(s)]

    if not uncached:
        return {s: _taxonomy_cache.get(s) for s in skill_names}

    # For 1-3 skills, do individual calls (more reliable)
    if len(uncached) <= 3:
        results = {}
        for skill in uncached:
            results[skill] = await generate_skill_taxonomy(skill, job_title, job_description)
        # Add cached skills
        for skill in skill_names:
            if skill not in results:
                results[skill] = _taxonomy_cache.get(skill)
        return results

    # For 4+ skills, batch them
    batch_prompt = f"""Generate taxonomy entries for these {len(uncached)} skills.
Job Title: {job_title or 'Not specified'}

Skills to define:
{chr(10).join(f'- {s}' for s in uncached)}

Return a JSON object where each key is the skill name and value is the taxonomy entry.
Each entry should have: category, aliases, depth_definitions, evidence_keywords, related_skills, transferability."""

    try:
        result = await llm_client.complete_json(
            system_prompt=_TAXONOMY_GENERATION_PROMPT,
            user_message=batch_prompt,
            max_tokens=min(800 * len(uncached), 4000),
        )

        results = {}
        for skill in uncached:
            skill_data = result.get(skill) or result.get(skill.lower()) or {}
            if skill_data:
                taxonomy = _validate_taxonomy(skill_data, skill)
            else:
                taxonomy = _fallback_taxonomy(skill)
            _taxonomy_cache.put(skill, taxonomy)
            results[skill] = taxonomy

        # Add cached skills
        for skill in skill_names:
            if skill not in results:
                results[skill] = _taxonomy_cache.get(skill)

        return results

    except Exception as e:
        logger.error(f"Batch taxonomy generation failed: {e}")
        results = {}
        for skill in skill_names:
            cached = _taxonomy_cache.get(skill)
            results[skill] = cached if cached else _fallback_taxonomy(skill)
        return results


def _validate_taxonomy(raw: dict, skill_name: str) -> Dict[str, Any]:
    """Validate and normalize a taxonomy entry from LLM output."""
    valid_categories = {
        "marketing", "finance", "hr", "legal", "healthcare",
        "supply_chain", "sales", "operations", "consulting",
        "real_estate", "media", "education", "general_business",
        "technology", "engineering", "design", "unknown",
    }

    category = raw.get("category", "unknown")
    if category not in valid_categories:
        category = "unknown"

    aliases = raw.get("aliases", [])
    if not isinstance(aliases, list):
        aliases = []
    aliases = [str(a).lower().strip() for a in aliases if a]

    depth_defs = raw.get("depth_definitions", {})
    if not isinstance(depth_defs, dict):
        depth_defs = {}
    # Ensure all 5 levels exist
    for level in ["1", "2", "3", "4", "5"]:
        if level not in depth_defs:
            depth_defs[level] = f"Depth {level} proficiency in {skill_name}"

    evidence_keywords = raw.get("evidence_keywords", [])
    if not isinstance(evidence_keywords, list):
        evidence_keywords = []
    evidence_keywords = [str(kw).lower().strip() for kw in evidence_keywords if kw]

    related_skills = raw.get("related_skills", [])
    if not isinstance(related_skills, list):
        related_skills = []

    transferability = raw.get("transferability", [])
    if not isinstance(transferability, list):
        transferability = []
    # Validate transferability entries
    valid_transfers = []
    for t in transferability:
        if isinstance(t, dict) and "skill" in t:
            coeff = t.get("coefficient", 0.5)
            if isinstance(coeff, (int, float)) and 0 < coeff <= 1.0:
                valid_transfers.append({
                    "skill": str(t["skill"]).lower().strip(),
                    "coefficient": round(float(coeff), 2),
                })

    return {
        "skill": skill_name,
        "category": category,
        "aliases": aliases,
        "depth_definitions": depth_defs,
        "evidence_keywords": evidence_keywords,
        "related_skills": [str(s).lower().strip() for s in related_skills],
        "transferability": valid_transfers,
        "is_dynamic": True,
        "confidence": 0.75,  # Dynamic taxonomies get slightly lower confidence
    }


def _fallback_taxonomy(skill_name: str) -> Dict[str, Any]:
    """Return a minimal taxonomy when LLM generation fails."""
    return {
        "skill": skill_name,
        "category": "unknown",
        "aliases": [skill_name.lower()],
        "depth_definitions": {
            "1": f"Basic awareness of {skill_name}",
            "2": f"Some practical experience with {skill_name}",
            "3": f"Regular professional use of {skill_name}",
            "4": f"Advanced proficiency in {skill_name}",
            "5": f"Expert level in {skill_name}",
        },
        "evidence_keywords": skill_name.lower().split(),
        "related_skills": [],
        "transferability": [],
        "is_dynamic": True,
        "confidence": 0.50,  # Low confidence for fallback
    }


def get_dynamic_evidence_aliases(skill_name: str) -> List[str]:
    """
    Get evidence aliases for a dynamically generated skill.
    Returns empty list if no dynamic taxonomy exists for this skill.
    """
    taxonomy = _taxonomy_cache.get(skill_name)
    if taxonomy:
        return taxonomy.get("aliases", []) + taxonomy.get("evidence_keywords", [])
    return []


def get_dynamic_transferability(skill_name: str) -> List[Dict]:
    """Get transferability mappings for a dynamically generated skill."""
    taxonomy = _taxonomy_cache.get(skill_name)
    if taxonomy:
        return taxonomy.get("transferability", [])
    return []


def is_dynamically_generated(skill_name: str) -> bool:
    """Check if a skill has a dynamically generated taxonomy."""
    return _taxonomy_cache.has(skill_name)
