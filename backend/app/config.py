"""
Application configuration via pydantic-settings.
All settings are read from environment variables.
"""
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/astragate"

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # LiteLLM Proxy
    LITELLM_URL: str = "http://litellm:4000"
    LITELLM_MASTER_KEY: str = "sk-litellm-master-key"

    # JWT
    JWT_SECRET: str = "change-me-in-production"
    JWT_REFRESH_SECRET: str = "change-me-refresh-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # Resend (email)
    RESEND_API_KEY: str = ""

    # Encryption
    DB_ENCRYPTION_KEY: str = "change-me-32-byte-encryption-key"

    # Frontend
    NEXT_PUBLIC_API_URL: Optional[str] = None

    # App
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"


settings = Settings()
