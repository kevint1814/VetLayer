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
    # Options: "openai" (default, uses GPT-4o Mini), "anthropic" (Claude)
    LLM_PROVIDER: str = "openai"
    LLM_MAX_TOKENS: int = 8000

    # OpenAI (default for development — GPT-4o Mini is cheap)
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Anthropic (for production quality)
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-5-20250929"

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

# ── Startup warnings ────────────────────────────────────────────────
if settings.SECRET_KEY == "change-me-in-production":
    _config_logger.warning(
        "⚠️  SECRET_KEY is still the default placeholder. "
        "Set a strong random value in .env before deploying to production."
    )
if settings.ADMIN_PASSWORD == "Admin@123":
    _config_logger.warning(
        "⚠️  ADMIN_PASSWORD is still the default. "
        "Set a strong password in .env before deploying to production."
    )
