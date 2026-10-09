"""Application configuration, loaded from environment variables and .env files."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Support running from backend/ as well as from the repository root.
_ENV_FILES = (_BACKEND_DIR / ".env", _REPO_ROOT / ".env")

# Backends app/db/session.py can drive (it picks the asyncio driver for us).
# Kept here so a bad DATABASE_URL is a configuration error at startup, and
# asserted to match app.db.session.SUPPORTED_BACKENDS in the tests.
SUPPORTED_DB_BACKENDS: tuple[str, ...] = ("sqlite", "postgresql", "postgres")


class Settings(BaseSettings):
    """Runtime settings. Every value comes from the environment (see .env.example)."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        extra="ignore",
    )

    app_env: str = "development"
    secret_key: str | None = None
    database_url: str = "sqlite:///./manovia.db"
    db_echo: bool = False
    llm_provider: str = "fake"
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    ollama_base_url: str | None = None
    field_encryption_key: str | None = None
    allowed_origins: str = "http://localhost:5173,http://localhost:3000"
    log_level: str = "INFO"

    emotion_provider: Literal["hf", "fallback", "fake"] = "fallback"
    emotion_model_id: str | None = None
    emotion_max_length: int = Field(default=128, ge=8, le=512)
    emotion_batch_size: int = Field(default=4, ge=1, le=32)
    emotion_timeout_seconds: float = Field(default=1.0, gt=0, le=60)
    emotion_cache_size: int = Field(default=256, ge=0, le=10000)

    @model_validator(mode="after")
    def _check_emotion_settings(self) -> "Settings":
        if self.emotion_provider == "hf" and not (self.emotion_model_id or "").strip():
            raise ValueError("EMOTION_MODEL_ID is required for EMOTION_PROVIDER=hf")
        return self

    # --- Authentication and consent (Day 4) ---
    # Password policy: length only, no composition theatre (NIST SP 800-63B).
    password_min_length: int = 10
    password_max_length: int = 256
    # Session lifetimes: short access tokens, week-long rotating refresh tokens.
    jwt_access_minutes: int = 15
    jwt_refresh_days: int = 7
    # Sliding-window request budgets per client IP: strict on /api/v1/auth/*,
    # moderate everywhere else under /api/. Disable only for load testing.
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = 10
    rate_limit_global_per_minute: int = 120
    # Account lockout: after login_max_failures consecutive failures the account
    # is locked for login_lockout_seconds, doubling per further failure up to
    # login_lockout_max_seconds. Clears on a successful sign-in.
    login_max_failures: int = 5
    login_lockout_seconds: int = 900
    login_lockout_max_seconds: int = 3600

    @model_validator(mode="after")
    def _require_secrets_in_production(self) -> "Settings":
        if self.app_env.strip().lower() != "production":
            return self
        if not self.secret_key:
            raise ValueError("SECRET_KEY must be set when APP_ENV=production")
        if not self.field_encryption_key:
            raise ValueError("FIELD_ENCRYPTION_KEY must be set when APP_ENV=production")
        return self

    @model_validator(mode="after")
    def _check_database_url(self) -> "Settings":
        url = self.database_url.strip()
        if not url:
            raise ValueError("DATABASE_URL must not be empty")
        scheme, _, _ = url.partition("://")
        backend = scheme.split("+", maxsplit=1)[0]
        if backend not in SUPPORTED_DB_BACKENDS:
            raise ValueError(
                "DATABASE_URL must point at sqlite or postgresql "
                f"(got {backend!r}; see .env.example for the accepted forms)"
            )
        return self

    @model_validator(mode="after")
    def _check_auth_settings(self) -> "Settings":
        """Fail at startup rather than at first login with nonsense limits."""
        if self.password_min_length < 1 or self.password_max_length < self.password_min_length:
            raise ValueError("password length bounds must be positive and ordered")
        if self.jwt_access_minutes < 1 or self.jwt_refresh_days < 1:
            raise ValueError("token lifetimes must be at least one unit")
        if self.rate_limit_auth_per_minute < 1 or self.rate_limit_global_per_minute < 1:
            raise ValueError("rate limits must be at least one request per window")
        if self.login_max_failures < 1:
            raise ValueError("login_max_failures must be at least 1")
        lock_ok = self.login_lockout_seconds >= 1
        lock_ok = lock_ok and self.login_lockout_max_seconds >= self.login_lockout_seconds
        if not lock_ok:
            raise ValueError("lockout windows must be positive and ordered")
        return self

    @property
    def allowed_origin_list(self) -> list[str]:
        """ALLOWED_ORIGINS parsed into a list of origins for CORS."""
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        """True when running as a deployed service (checks are stricter there)."""
        return self.app_env.strip().lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()
