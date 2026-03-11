"""
LLM Client — multi-provider wrapper supporting OpenAI and Anthropic.
Centralized so all services use consistent settings, error handling, and logging.

Switch providers via LLM_PROVIDER env variable:
  - "openai"    → GPT-4o Mini (default, cheap, good for dev)
  - "anthropic" → Claude Sonnet (production quality)
"""

import json
import re
import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


def _try_repair_json(text: str) -> Optional[dict]:
    """
    Attempt to repair truncated JSON from LLM responses.
    Common failure mode: response hits token limit and gets cut mid-string/mid-object.
    """
    # Strategy 1: Try closing any open strings, arrays, and objects
    repaired = text.rstrip()

    # Close any unterminated string
    quote_count = repaired.count('"') - repaired.count('\\"')
    if quote_count % 2 != 0:
        repaired += '"'

    # Count open brackets/braces
    open_brackets = repaired.count('[') - repaired.count(']')
    open_braces = repaired.count('{') - repaired.count('}')

    # Remove any trailing comma before closing
    repaired = re.sub(r',\s*$', '', repaired)

    # Close arrays and objects
    repaired += ']' * max(0, open_brackets)
    repaired += '}' * max(0, open_braces)

    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Trim from the end to find a valid JSON prefix
    for end_pos in range(len(text) - 1, max(0, len(text) - 500), -1):
        chunk = text[:end_pos]
        chunk = re.sub(r',\s*$', '', chunk.rstrip())
        ob = chunk.count('[') - chunk.count(']')
        oc = chunk.count('{') - chunk.count('}')
        attempt = chunk + ']' * max(0, ob) + '}' * max(0, oc)
        try:
            result = json.loads(attempt)
            logger.warning(f"JSON repaired by trimming {len(text) - end_pos} chars from end")
            return result
        except json.JSONDecodeError:
            continue

    return None


class LLMClient:
    """
    Multi-provider LLM wrapper.
    All VetLayer services go through this client.
    """

    def __init__(self):
        self.provider = settings.LLM_PROVIDER
        self.max_tokens = settings.LLM_MAX_TOKENS
        self._openai_client = None
        self._anthropic_client = None

        logger.info(f"LLM provider: {self.provider}")

    # ── Client initialization ────────────────────────────────────────

    async def _get_openai_client(self):
        if self._openai_client is None:
            try:
                from openai import AsyncOpenAI
                self._openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            except ImportError:
                raise RuntimeError("openai package not installed. Run: pip install openai")
        return self._openai_client

    async def _get_anthropic_client(self):
        if self._anthropic_client is None:
            try:
                import anthropic
                self._anthropic_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
            except ImportError:
                raise RuntimeError("anthropic package not installed. Run: pip install anthropic")
        return self._anthropic_client

    # ── Core completion methods ──────────────────────────────────────

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: Optional[int] = None,
        temperature: float = 0.3,
    ) -> str:
        """Send a message and return the text response."""
        tokens = max_tokens or self.max_tokens

        if self.provider == "openai":
            return await self._complete_openai(system_prompt, user_message, tokens, temperature, json_mode=False)
        elif self.provider == "anthropic":
            return await self._complete_anthropic(system_prompt, user_message, tokens, temperature)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    async def complete_json(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: Optional[int] = None,
    ) -> dict:
        """Send a message expecting JSON response. Parses and returns a dict."""
        text = await self._complete_json_raw(
            system_prompt=system_prompt + "\n\nRespond with valid JSON only. No markdown, no explanation.",
            user_message=user_message,
            max_tokens=max_tokens,
        )

        # Strip markdown code fences if present
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed, attempting repair: {e}")
            repaired = _try_repair_json(text)
            if repaired is not None:
                logger.info("Successfully repaired truncated JSON response")
                return repaired
            logger.error(f"JSON repair failed. Response length: {len(text)} chars\nFirst 500 chars: {text[:500]}")
            raise ValueError(f"LLM returned invalid JSON: {e}")

    async def _complete_json_raw(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Internal: send a completion expecting JSON, with provider-specific json_mode."""
        tokens = max_tokens or self.max_tokens

        if self.provider == "openai":
            return await self._complete_openai(system_prompt, user_message, tokens, 0.1, json_mode=True)
        elif self.provider == "anthropic":
            return await self._complete_anthropic(system_prompt, user_message, tokens, 0.1)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    # ── Provider implementations ─────────────────────────────────────

    async def _complete_openai(
        self, system_prompt: str, user_message: str, max_tokens: int, temperature: float,
        json_mode: bool = False,
    ) -> str:
        client = await self._get_openai_client()
        kwargs = dict(
            model=settings.OPENAI_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    async def _complete_anthropic(
        self, system_prompt: str, user_message: str, max_tokens: int, temperature: float
    ) -> str:
        client = await self._get_anthropic_client()
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text


# Singleton instance
llm_client = LLMClient()
