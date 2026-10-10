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

# Providers the LLM chain can lead with (Day 10). "fake" is the offline
# double the test suite uses; the chain always ends at a canned reply, so any
# of these being unusable is a degradation, never a 500.
LLM_PROVIDER_CHOICES: tuple[str, ...] = ("anthropic", "ollama", "fake")


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

    # --- LLM provider layer (Day 10) ---

    # Which provider leads the conversation: anthropic | ollama | fake.
    # "fake" is deterministic and offline; it is what the test suite uses.
    # The chain always ends at a canned supportive reply, so a missing key or
    # a dead provider degrades instead of returning a 500 (ADR 0010).
    # llm_provider / anthropic_api_key / anthropic_model / ollama_base_url are
    # declared above with the other connection settings.
    llm_timeout_seconds: float = 15.0
    llm_max_attempts: int = 3
    llm_retry_base_seconds: float = 0.4
    llm_retry_max_seconds: float = 8.0
    llm_circuit_failure_threshold: int = 3
    llm_circuit_cooldown_seconds: float = 60.0
    # Generation defaults. max_tokens is also the cost ceiling per reply.
    llm_max_tokens: int = 400
    llm_temperature: float = 0.7
    # Input guard: long messages are truncated, never rejected, and the
    # conversation window keeps only the most recent turns that fit the budget.
    llm_max_input_chars: int = 8000
    llm_window_turns: int = 12
    llm_window_tokens: int = 3000
    # Which versioned prompt file under app/content/prompts/ to load.
    llm_prompt_version: str = "system_v1"
    # Local Ollama model id (env OLLAMA_MODEL). Empty means "ask the server
    # what it has" and is only valid for the fallback provider.
    ollama_model: str = ""
    # PII redaction (app/services/nlp/redaction.py). Names stay off by
    # default: an NER pass is slower and false-positives on common Indian
    # given names that are also ordinary words.
    llm_redact_pii: bool = True
    llm_redact_names: bool = False

    # --- Chat orchestrator (Day 11) ---
    # One message may not be longer than this (characters, after NFC
    # normalisation). Longer is a 422, not a silent truncation: the safety
    # engine and the model must see exactly what the person wrote.
    chat_max_message_chars: int = 4000
    # Sliding-window budget of chat messages per *user* (not per IP: a shared
    # network must not throttle one person and one person must not borrow
    # another's budget). Skipped when rate_limit_enabled is false.
    chat_rate_limit_per_minute: int = 30
    # How many prior messages the prompt may carry (the token guard trims
    # further). "Short window": the model needs the thread, not the archive.
    chat_history_turns: int = 10
    # Ephemeral (not-saved) sessions live in process memory and expire after
    # this much inactivity. 30 minutes by brief.
    chat_ephemeral_ttl_seconds: int = 1800
    chat_ephemeral_max_sessions: int = 5000
    chat_ephemeral_max_turns: int = 60

    # --- Safety ML ensemble (Day 9) ---
    # The ML classifier can only RAISE the level the rules engine found, never
    # lower it (docs/safety-design.md §12). With the flag off the pipeline is
    # rules-only, which is also how the test suite runs by default.
    safety_ml_enabled: bool = True
    # Minimum top-class calibrated probability for the ML level to be trusted
    # enough to raise the assessment. All three thresholds were chosen on the
    # dev split only (evals/tune_safety_thresholds.py); the test split was
    # never touched. These defaults are the recall-first operating point
    # (dev HIGH+IMMINENT recall 1.00, target >= 0.97); the cost — a high
    # crisis-card rate on benign text at this dataset size — is documented in
    # docs/safety-design.md §12.6, along with the precision-leaning
    # alternative (crisis_mass_floor 0.45-0.50).
    safety_ml_min_confidence: float = 0.70
    # If the HIGH+IMMINENT probability mass reaches this floor, the ensemble
    # raises to HIGH even when the model is torn between the two crisis levels
    # (the top class alone undersells the evidence).
    safety_ml_crisis_mass_floor: float = 0.30
    # Below the confidence threshold, if the crisis mass reaches this lower
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
        if not 0.0 <= self.safety_ml_crisis_mass_floor <= 1.0:
            raise ValueError("safety_ml_crisis_mass_floor must be in [0, 1]")
        if not 0.0 <= self.safety_ml_suspicion_floor <= 1.0:
            raise ValueError("safety_ml_suspicion_floor must be in [0, 1]")
        if self.safety_ml_crisis_mass_floor < self.safety_ml_suspicion_floor:
            raise ValueError("safety_ml_crisis_mass_floor must be >= safety_ml_suspicion_floor")
        return self

    @model_validator(mode="after")
    def _check_llm_settings(self) -> "Settings":
        """Catch an unusable LLM configuration at startup, not mid-conversation."""
        if self.llm_provider.strip().casefold() not in LLM_PROVIDER_CHOICES:
            raise ValueError(
                f"LLM_PROVIDER must be one of {', '.join(LLM_PROVIDER_CHOICES)} "
                f"(got {self.llm_provider!r})"
            )
        if self.llm_timeout_seconds <= 0:
            raise ValueError("llm_timeout_seconds must be positive")
        if self.llm_max_attempts < 1:
            raise ValueError("llm_max_attempts must be at least 1")
        if self.llm_retry_base_seconds <= 0 or self.llm_retry_max_seconds <= 0:
            raise ValueError("retry delays must be positive")
        if self.llm_retry_max_seconds < self.llm_retry_base_seconds:
            raise ValueError("llm_retry_max_seconds must be >= llm_retry_base_seconds")
        if self.llm_circuit_failure_threshold < 1:
            raise ValueError("llm_circuit_failure_threshold must be at least 1")
        if self.llm_circuit_cooldown_seconds < 0:
            raise ValueError("llm_circuit_cooldown_seconds must not be negative")
        if self.llm_max_tokens < 1:
            raise ValueError("llm_max_tokens must be at least 1")
        if not 0.0 <= self.llm_temperature <= 2.0:
            raise ValueError("llm_temperature must be within [0, 2]")
        if self.llm_max_input_chars < 1:
            raise ValueError("llm_max_input_chars must be at least 1")
        if self.llm_window_turns < 1:
            raise ValueError("llm_window_turns must be at least 1")
        if self.llm_window_tokens < 1:
            raise ValueError("llm_window_tokens must be at least 1")
        return self

    @model_validator(mode="after")
    def _check_chat_settings(self) -> "Settings":
        """Catch an unusable chat configuration at startup."""
        if self.chat_max_message_chars < 1:
            raise ValueError("chat_max_message_chars must be at least 1")
        if self.chat_rate_limit_per_minute < 1:
            raise ValueError("chat_rate_limit_per_minute must be at least 1")
        if self.chat_history_turns < 0:
            raise ValueError("chat_history_turns must not be negative")
        if self.chat_ephemeral_ttl_seconds < 1:
            raise ValueError("chat_ephemeral_ttl_seconds must be at least 1")
        if self.chat_ephemeral_max_sessions < 1 or self.chat_ephemeral_max_turns < 2:
            raise ValueError("ephemeral session limits are too small to hold a conversation")
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
