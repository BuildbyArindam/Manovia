"""Application configuration, loaded from environment variables and .env files."""

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
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
