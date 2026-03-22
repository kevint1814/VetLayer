"""
LLM Client — multi-provider wrapper supporting Groq, OpenAI, and Anthropic.
Centralized so all services use consistent settings, error handling, and logging.

Switch providers via LLM_PROVIDER env variable:
  - "groq"      → Llama 3.3 70B on Groq (default — fast, strong instruction-following)
  - "openai"    → GPT-4o Mini (reliable JSON, good reasoning — automatic fallback)
  - "anthropic" → Claude Sonnet (production quality)

Fallback: When LLM_FALLBACK_ENABLED=true and the primary provider fails,
the client automatically retries with LLM_FALLBACK_PROVIDER (default: openai).
"""

import json
import re
import logging
import asyncio
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


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences that LLMs sometimes wrap around JSON."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


class LLMClient:
    """
    Multi-provider LLM wrapper with automatic fallback.
    All VetLayer services go through this client.

    Provider priority:
      1. Primary (LLM_PROVIDER) — tried first
      2. Fallback (LLM_FALLBACK_PROVIDER) — tried if primary fails and fallback is enabled
    """

    def __init__(self):
        self.provider = settings.LLM_PROVIDER
        self.max_tokens = settings.LLM_MAX_TOKENS
        self.fallback_enabled = settings.LLM_FALLBACK_ENABLED
        self.fallback_provider = settings.LLM_FALLBACK_PROVIDER
        self._groq_client = None
        self._openai_client = None
        self._anthropic_client = None

        logger.info(
            f"LLM provider: {self.provider}"
            + (f" (fallback: {self.fallback_provider})" if self.fallback_enabled else " (no fallback)")
        )

    # ── Client initialization ────────────────────────────────────────

    async def _get_groq_client(self):
        if self._groq_client is None:
            try:
                from groq import AsyncGroq
                self._groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)
            except ImportError:
                raise RuntimeError("groq package not installed. Run: pip install groq")
        return self._groq_client

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
        temperature: float = 0.0,
    ) -> str:
        """Send a message and return the text response. Falls back on failure."""
        tokens = max_tokens or self.max_tokens

        try:
            return await self._dispatch(self.provider, system_prompt, user_message, tokens, temperature, json_mode=False)
        except Exception as primary_err:
            if self.fallback_enabled and self.fallback_provider != self.provider:
                logger.warning(
                    f"Primary provider '{self.provider}' failed: {primary_err}. "
                    f"Falling back to '{self.fallback_provider}'..."
                )
                return await self._dispatch(
                    self.fallback_provider, system_prompt, user_message, tokens, temperature, json_mode=False
                )
            raise

    async def complete_json(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: Optional[int] = None,
    ) -> dict:
        """Send a message expecting JSON response. Parses and returns a dict."""
        json_system = system_prompt + "\n\nRespond with valid JSON only. No markdown, no explanation."

        text = await self._complete_json_with_fallback(json_system, user_message, max_tokens)

        # Strip markdown code fences if present
        text = _strip_markdown_fences(text)

        # Handle empty/whitespace responses (Groq edge case)
        if not text or text.strip() in ("", "null", "None"):
            logger.warning("LLM returned empty/null response, returning empty dict")
            return {}

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

    async def _complete_json_with_fallback(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Internal: get JSON text from LLM with fallback.
        On primary failure OR unparseable JSON, falls back to secondary provider.
        """
        tokens = max_tokens or self.max_tokens

        try:
            text = await self._dispatch(self.provider, system_prompt, user_message, tokens, 0.0, json_mode=True)

            # Quick-validate: if the primary response isn't even parseable after
            # stripping fences, fall back immediately instead of waiting for repair
            stripped = _strip_markdown_fences(text)
            if stripped and stripped.strip() not in ("", "null", "None"):
                try:
                    json.loads(stripped)
                    return text  # Valid JSON — use primary response
                except json.JSONDecodeError:
                    # Try repair first before falling back
                    repaired = _try_repair_json(stripped)
                    if repaired is not None:
                        return json.dumps(repaired)
                    # Repair failed — fall back
                    if self.fallback_enabled and self.fallback_provider != self.provider:
                        logger.warning(
                            f"Primary provider '{self.provider}' returned unparseable JSON. "
                            f"Falling back to '{self.fallback_provider}'..."
                        )
                        return await self._dispatch(
                            self.fallback_provider, system_prompt, user_message, tokens, 0.0, json_mode=True
                        )
                    return text  # No fallback available, let caller handle

            # Empty response — fall back
            if self.fallback_enabled and self.fallback_provider != self.provider:
                logger.warning(
                    f"Primary provider '{self.provider}' returned empty response. "
                    f"Falling back to '{self.fallback_provider}'..."
                )
                return await self._dispatch(
                    self.fallback_provider, system_prompt, user_message, tokens, 0.0, json_mode=True
                )
            return text

        except Exception as primary_err:
            if self.fallback_enabled and self.fallback_provider != self.provider:
                logger.warning(
                    f"Primary provider '{self.provider}' failed: {primary_err}. "
                    f"Falling back to '{self.fallback_provider}'..."
                )
                return await self._dispatch(
                    self.fallback_provider, system_prompt, user_message, tokens, 0.0, json_mode=True
                )
            raise

    # ── Provider dispatch ─────────────────────────────────────────────

    async def _dispatch(
        self, provider: str, system_prompt: str, user_message: str,
        max_tokens: int, temperature: float, json_mode: bool,
    ) -> str:
        """Route to the correct provider implementation."""
        if provider == "groq":
            return await self._complete_groq(system_prompt, user_message, max_tokens, temperature, json_mode)
        elif provider == "openai":
            return await self._complete_openai(system_prompt, user_message, max_tokens, temperature, json_mode)
        elif provider == "anthropic":
            return await self._complete_anthropic(system_prompt, user_message, max_tokens, temperature)
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    # ── Provider implementations ─────────────────────────────────────

    async def _complete_groq(
        self, system_prompt: str, user_message: str, max_tokens: int, temperature: float,
        json_mode: bool = False,
    ) -> str:
        """
        Groq — Llama 3.3 70B via Groq LPU inference.
        Uses OpenAI-compatible API format. Supports JSON mode.
        Includes rate-limit retry with exponential backoff.
        """
        client = await self._get_groq_client()
        kwargs = dict(
            model=settings.GROQ_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        # Retry with backoff for rate limits (Groq can be bursty)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await client.chat.completions.create(**kwargs)
                return response.choices[0].message.content
            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = "rate_limit" in error_str or "429" in error_str
                is_overloaded = "overloaded" in error_str or "503" in error_str

                if (is_rate_limit or is_overloaded) and attempt < max_retries - 1:
                    wait = (attempt + 1) * 2  # 2s, 4s backoff
                    logger.warning(f"Groq rate limited (attempt {attempt + 1}/{max_retries}), waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                raise  # Not a retryable error, or final attempt

        # Should not reach here, but just in case
        raise RuntimeError("Groq: max retries exceeded")

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
