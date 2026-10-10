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

# Which emotion analyzer leads the chain (Day 6). "auto" and "hf" both put the
# Hugging Face model first and differ only in intent; the lexicon fallbacks are
# attached either way. "fake" is for tests. See
# docs/adr/0006-emotion-model.md for why the model is a setting, not a constant.
EMOTION_ANALYZER_CHOICES: tuple[str, ...] = ("auto", "hf", "keyword", "sentiment", "fake")


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

    # --- Emotion and sentiment analysis (Day 6) ---
    # Which analyzer leads the chain; lexicon fallbacks are attached either way.
    emotion_analyzer: str = "auto"
    # Model id only — never a hard-coded path or a local checkpoint (AGENTS.md:
    # never hard-code model names). See docs/adr/0006-emotion-model.md.
    emotion_model_id: str = "SamLowe/roberta-base-go_emotions"
    # CPU-only by default: this service must run on a small container, and a
    # GPU is a deployment decision, not a default.
    emotion_device: str = "cpu"
    # Token budget per text. Longer input is truncated, not rejected.
    emotion_max_length: int = 256
    # Small batches: CPU inference does not benefit from large ones, and a big
    # batch would hold the worker thread for too long.
    emotion_batch_size: int = 8
    # In-memory LRU of analysis results, keyed by a hash of the text.
    emotion_cache_size: int = 512
    # Circuit breaker: after this many failures the model is skipped for
    # emotion_cooldown_seconds instead of failing every request.
    emotion_failure_threshold: int = 2
    emotion_cooldown_seconds: float = 60.0
    # If the model's latency average exceeds this, the chain stops routing to
    # it: a stalled companion is worse than a cruder reading.
    emotion_slow_ms: float = 1500.0

    # --- Safety ML ensemble (Day 9) ---
    # The ML classifier can only RAISE the level the rules engine found, never
    # lower it (docs/safety-design.md §12). With the flag off the pipeline is
    # rules-only, which is also how the test suite runs by default.
    safety_ml_enabled: bool = True
    # Minimum top-class calibrated probability for the ML level to be trusted
    # enough to raise the assessment. Chosen on the dev split only
    # (evals/tune_safety_thresholds.py); the test split was never touched.
    safety_ml_min_confidence: float = 0.45
    # Below that confidence, if the HIGH+IMMINENT probability mass reaches this
    # floor, the ensemble plays safe: treat as MEDIUM, soft check-in policy.
    safety_ml_suspicion_floor: float = 0.25
    # Where the trained artifact lives. Empty means the shipped artifact under
    # app/ml_artifacts/. A missing artifact degrades to rules-only, never to a
    # startup failure.
    safety_ml_artifact_dir: str = ""

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

    @model_validator(mode="after")
    def _check_emotion_settings(self) -> "Settings":
        """Fail at startup on an unusable NLP configuration.

        A typo in ``EMOTION_ANALYZER`` must not silently mean "no model, no
        fallbacks" three weeks later, and a zero token budget must not reach
        the tokenizer.
        """
        choice = self.emotion_analyzer.strip().casefold()
        if choice not in EMOTION_ANALYZER_CHOICES:
            raise ValueError(
                "EMOTION_ANALYZER must be one of "
                f"{', '.join(EMOTION_ANALYZER_CHOICES)} (got {self.emotion_analyzer!r})"
            )
        if choice in {"auto", "hf"} and not self.emotion_model_id.strip():
            raise ValueError("EMOTION_MODEL_ID must be set when EMOTION_ANALYZER uses the model")
        if self.emotion_max_length < 8:
            raise ValueError("emotion_max_length must be at least 8 tokens")
        if self.emotion_batch_size < 1:
            raise ValueError("emotion_batch_size must be at least 1")
        if self.emotion_cache_size < 0:
            raise ValueError("emotion_cache_size must be 0 or greater")
        if self.emotion_failure_threshold < 1:
            raise ValueError("emotion_failure_threshold must be at least 1")
        if self.emotion_cooldown_seconds < 0:
            raise ValueError("emotion_cooldown_seconds must not be negative")
        if self.emotion_slow_ms <= 0:
            raise ValueError("emotion_slow_ms must be positive")
        return self

    @model_validator(mode="after")
    def _check_safety_ml_settings(self) -> "Settings":
        """The ensemble thresholds must be usable probabilities."""
        if not 0.0 < self.safety_ml_min_confidence <= 1.0:
            raise ValueError("safety_ml_min_confidence must be in (0, 1]")
        if not 0.0 <= self.safety_ml_suspicion_floor <= 1.0:
            raise ValueError("safety_ml_suspicion_floor must be in [0, 1]")
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
