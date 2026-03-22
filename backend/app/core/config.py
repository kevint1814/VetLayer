"""
Application configuration — loaded from environment variables.
"""

import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

_config_logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # ── General ──────────────────────────────────────────────────────
    PROJECT_NAME: str = "VetLayer"
    VERSION: str = "0.1.0"
    DEBUG: bool = False  # Default to False for safety; set DEBUG=true in .env for dev

    # ── Database ─────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://vetlayer:vetlayer@127.0.0.1:5432/vetlayer"
    DATABASE_ECHO: bool = False

    # ── CORS ─────────────────────────────────────────────────────────
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # ── LLM Provider ─────────────────────────────────────────────────
    # Options: "groq" (fast, Llama 3.3 70B), "openai" (GPT-4o Mini), "anthropic" (Claude)
    LLM_PROVIDER: str = "groq"
    LLM_MAX_TOKENS: int = 8000

    # Groq (primary — fast inference via Llama 3.3 70B)
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # OpenAI (fallback — GPT-4o Mini, reliable JSON)
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Anthropic (alternative production quality)
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-5-20250929"

    # ── Fallback behavior ──────────────────────────────────────────
    # When True, if primary provider fails, retry with OpenAI as fallback
    LLM_FALLBACK_ENABLED: bool = True
    LLM_FALLBACK_PROVIDER: str = "openai"

    # ── File uploads ─────────────────────────────────────────────────
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 10

    # ── Security / Auth ──────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_ALGORITHM: str = "HS256"
    MAX_FAILED_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_DURATION_MINUTES: int = 15

    # ── Admin seed (created on first startup if no users exist) ────
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "Admin@123"
    ADMIN_FULL_NAME: str = "System Administrator"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()

# ── Startup safety checks ───────────────────────────────────────────
# In DEBUG mode: warn about insecure defaults so devs can iterate fast.
# In production (DEBUG=False): refuse to start with default secrets.

_INSECURE_SECRET_KEY = settings.SECRET_KEY == "change-me-in-production"
_INSECURE_ADMIN_PW = settings.ADMIN_PASSWORD == "Admin@123"

if settings.DEBUG:
    # Dev mode — warn only
    if _INSECURE_SECRET_KEY:
        _config_logger.warning(
            "⚠️  SECRET_KEY is the default placeholder. "
            "Set a strong random value in .env before deploying to production."
        )
    if _INSECURE_ADMIN_PW:
        _config_logger.warning(
            "⚠️  ADMIN_PASSWORD is the default. "
            "Set a strong password in .env before deploying to production."
        )
else:
    # Production mode — block startup with insecure defaults
    _fatal_errors = []
    if _INSECURE_SECRET_KEY:
        _fatal_errors.append(
            "SECRET_KEY is still 'change-me-in-production'. "
            "Set a cryptographically random value (e.g. python -c \"import secrets; print(secrets.token_urlsafe(64))\")."
        )
    if _INSECURE_ADMIN_PW:
        _fatal_errors.append(
            "ADMIN_PASSWORD is still 'Admin@123'. Set a strong password in .env."
        )
    if _fatal_errors:
        for err in _fatal_errors:
            _config_logger.critical(f"🛑 FATAL: {err}")
        raise SystemExit(
            "\n🛑 VetLayer refused to start: insecure default secrets detected.\n"
            "Either set proper values in .env or enable DEBUG=true for development.\n"
            "Details:\n  - " + "\n  - ".join(_fatal_errors)
        )
