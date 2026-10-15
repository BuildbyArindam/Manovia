"""Unit tests for application settings."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_defaults_load_in_development_without_secret_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("APP_ENV", "SECRET_KEY", "DATABASE_URL", "DB_ECHO", "FIELD_ENCRYPTION_KEY"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.app_env == "development"
    assert settings.secret_key is None
    assert settings.is_production is False
    # A SQLite default keeps a fresh clone running with no configuration at all.
    assert settings.database_url == "sqlite:///./manovia.db"
    assert settings.db_echo is False
    assert settings.field_encryption_key is None


def test_a_fresh_clone_env_from_the_example_file_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    """`cp .env.example .env` must yield a bootable configuration (Day 30).

    The example file ships every key blank. Found during the v1.0.0 release
    checklist: blank values were parsed as empty *strings*, so typed fields
    (RAG_RETRIEVAL_LIMIT=) failed validation and a fresh clone could not even
    run `alembic upgrade head`. Blank now means "unset, use the default"
    (env_ignore_empty).
    """
    example = Path(__file__).resolve().parents[3] / ".env.example"
    assert example.is_file(), "the repository must ship .env.example"
    # Nothing from the developer's real environment may mask a blank line.
    for line in example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            monkeypatch.delenv(stripped.split("=", 1)[0].strip(), raising=False)

    settings = Settings(_env_file=example)

    # The typed fields that broke on the fresh clone resolve to their defaults.
    assert settings.rag_retrieval_limit == 3
    assert settings.app_env == "development"
    assert settings.database_url == "sqlite:///./manovia.db"


def test_a_blank_env_value_means_unset_not_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blank environment value falls back to the default (Day 30)."""
    monkeypatch.setenv("RAG_RETRIEVAL_LIMIT", "")
    monkeypatch.setenv("LOG_LEVEL", "")
    settings = Settings(_env_file=None)
    assert settings.rag_retrieval_limit == 3
    assert settings.log_level == "INFO"


def test_production_without_secret_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(_env_file=None)


def test_production_without_a_field_encryption_key_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No deployment may run with personal text it cannot decrypt, or would encrypt."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "super-secret-value")
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    with pytest.raises(ValidationError, match="FIELD_ENCRYPTION_KEY"):
        Settings(_env_file=None)


def test_production_with_both_secrets_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "super-secret-value")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "a-field-key")
    settings = Settings(_env_file=None)
    assert settings.app_env == "production"
    assert settings.secret_key == "super-secret-value"
    assert settings.is_production is True


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///:memory:",
        "postgresql+asyncpg://manovia@db:5432/manovia",
        "postgres://manovia@db:5432/manovia",
        "sqlite:////tmp/manovia.db",
    ],
)
def test_supported_database_urls_load(url: str) -> None:
    assert Settings(_env_file=None, database_url=url).database_url == url


def test_an_unsupported_database_url_is_refused() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL must point at sqlite or postgresql"):
        Settings(_env_file=None, database_url="mysql+pymysql://localhost/app")


def test_an_empty_database_url_is_refused() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL must not be empty"):
        Settings(_env_file=None, database_url="   ")


def test_allowed_origins_are_split_and_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example, https://b.example ,")
    settings = Settings(_env_file=None)
    assert settings.allowed_origin_list == ["https://a.example", "https://b.example"]


def test_settings_read_values_from_dotenv_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("APP_ENV", "SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=production\n"
        "SECRET_KEY=from-dotenv-file\n"
        "FIELD_ENCRYPTION_KEY=also-from-dotenv\n"
        "DATABASE_URL=postgresql+asyncpg://manovia@db:5432/manovia\n"
    )
    settings = Settings(_env_file=env_file)
    assert settings.app_env == "production"
    assert settings.secret_key == "from-dotenv-file"
    assert settings.field_encryption_key == "also-from-dotenv"
    assert settings.database_url == "postgresql+asyncpg://manovia@db:5432/manovia"


def test_a_local_dotenv_file_never_reaches_a_test() -> None:
    """Regression guard for the test harness itself.

    The repository may contain a real ``.env`` (backend/.env or ../.env) while
    the suite runs, and several callers construct ``Settings()`` directly. If the
    isolation in ``tests/conftest.py`` were dropped, those values would leak in
    and a green run would stop meaning "the code works" — so this test asserts
    the plain defaults a developer's file cannot provide.
    """
    settings = Settings()
    assert settings.field_encryption_key is None
    assert settings.secret_key is None
    assert settings.app_env == "development"


def test_get_settings_caches_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    first = get_settings()
    monkeypatch.setenv("APP_ENV", "development")
    assert get_settings() is first  # cached: later env changes are not picked up
    get_settings.cache_clear()
    assert get_settings().app_env == "development"


def test_day_4_auth_defaults() -> None:
    settings = Settings(_env_file=None)
    # Password policy: length only (NIST SP 800-63B), no composition rules.
    assert settings.password_min_length == 10
    assert settings.password_max_length == 256
    # 15-minute access tokens, week-long rotating refresh tokens.
    assert settings.jwt_access_minutes == 15
    assert settings.jwt_refresh_days == 7
    # Strict on auth, moderate globally, on by default.
    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_auth_per_minute == 10
    assert settings.rate_limit_global_per_minute == 120
    # Lock after 5 failures for 15 minutes, doubling to a 1-hour cap.
    assert settings.login_max_failures == 5
    assert settings.login_lockout_seconds == 900
    assert settings.login_lockout_max_seconds == 3600


def test_absurd_auth_settings_are_refused_at_startup() -> None:
    cases: list[dict[str, Any]] = [
        {"password_min_length": 0},
        {"password_min_length": 20, "password_max_length": 10},
        {"jwt_access_minutes": 0},
        {"jwt_refresh_days": 0},
        {"rate_limit_auth_per_minute": 0},
        {"rate_limit_global_per_minute": 0},
        {"login_max_failures": 0},
        {"login_lockout_seconds": 0},
        {"login_lockout_seconds": 100, "login_lockout_max_seconds": 50},
    ]
    for kwargs in cases:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **kwargs)


def test_day_6_emotion_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.emotion_analyzer == "auto"
    # A model id must be configured, never hard-coded in a call site.
    assert settings.emotion_model_id
    assert settings.emotion_device == "cpu"
    assert settings.emotion_max_length == 256
    assert settings.emotion_batch_size == 8
    assert settings.emotion_cache_size == 512
    assert settings.emotion_failure_threshold == 2
    assert settings.emotion_cooldown_seconds == 60.0
    assert settings.emotion_slow_ms == 1500.0


def test_a_lexicon_only_setup_does_not_need_a_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # An explicitly empty model id is still accepted for lexicon-only setups…
    settings = Settings(_env_file=None, emotion_analyzer="keyword", emotion_model_id="")
    assert settings.emotion_analyzer == "keyword"
    assert settings.emotion_model_id == ""

    # …while a *blank environment value* means "unset" (Day 30,
    # env_ignore_empty): the default model id applies, and a keyword setup
    # still never loads it.
    monkeypatch.setenv("EMOTION_ANALYZER", "keyword")
    monkeypatch.setenv("EMOTION_MODEL_ID", "")
    from_env = Settings(_env_file=None)
    assert from_env.emotion_analyzer == "keyword"
    assert from_env.emotion_model_id == "SamLowe/roberta-base-go_emotions"


@pytest.mark.parametrize("choice", ["auto", "hf", "baseline", "keyword", "sentiment", "fake"])
def test_every_documented_analyzer_choice_is_accepted(choice: str) -> None:
    settings = Settings(_env_file=None, emotion_analyzer=choice)
    assert settings.emotion_analyzer == choice


def test_a_developers_ambient_safety_ml_choice_cannot_reach_a_test() -> None:
    """Day 33: `SAFETY_ML` must be neutralised like the thresholds around it.

    `clean_env` in ``tests/conftest.py`` deletes the Day-9 safety keys so a
    developer's shell cannot flip what a test proves. `SAFETY_ML` was added on
    Day 33 and was missing from that list, so exporting `SAFETY_ML=trained` to
    try the real model would have silently moved every test that builds
    ``Settings()`` onto the trained path. This asserts the *fixture's* contract,
    not the setting's: it is the isolation that is under test.
    """

    from tests.conftest import _ENV_KEYS

    assert "SAFETY_ML" in _ENV_KEYS
    assert "SAFETY_ML_TRAINED_ARTIFACT_DIR" in _ENV_KEYS


def test_the_safety_ml_choice_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAFETY_ML", "trained")
    assert Settings(_env_file=None).safety_ml == "trained"
    monkeypatch.setenv("SAFETY_ML", "baseline")
    assert Settings(_env_file=None).safety_ml == "baseline"


def test_an_unknown_safety_ml_choice_is_refused_at_startup() -> None:
    """A typo in SAFETY_ML must fail at startup, not silently mean baseline."""

    with pytest.raises(ValidationError, match="safety_ml"):
        Settings(_env_file=None, safety_ml="imaginary")


def test_the_trained_choice_needs_its_own_directory() -> None:
    """``trained`` is not a synonym for the hub model: it needs an artefact."""

    with pytest.raises(ValidationError, match="EMOTION_TRAINED_DIR"):
        Settings(_env_file=None, emotion_analyzer="trained")
    configured = Settings(
        _env_file=None, emotion_analyzer="trained", emotion_trained_dir="ml/artifacts/emotion/v1"
    )
    assert configured.emotion_trained_path.name == "v1"
    assert configured.emotion_trained_max_length == 128


def test_an_absurd_trained_token_budget_is_refused() -> None:
    with pytest.raises(ValidationError, match="emotion_trained_max_length"):
        Settings(
            _env_file=None,
            emotion_analyzer="keyword",
            emotion_trained_max_length=2,
        )


def test_an_unknown_analyzer_choice_is_refused_at_startup() -> None:
    """A typo must fail loudly, not silently mean no analyzer."""
    with pytest.raises(ValidationError, match="EMOTION_ANALYZER"):
        Settings(_env_file=None, emotion_analyzer="magic")


def test_a_model_choice_without_a_model_id_is_refused() -> None:
    with pytest.raises(ValidationError, match="EMOTION_MODEL_ID"):
        Settings(_env_file=None, emotion_analyzer="hf", emotion_model_id="  ")


@pytest.mark.parametrize(
    "field",
    [
        "emotion_max_length",
        "emotion_batch_size",
        "emotion_cache_size",
        "emotion_failure_threshold",
        "emotion_cooldown_seconds",
        "emotion_slow_ms",
    ],
)
def test_absurd_emotion_settings_are_refused_at_startup(field: str) -> None:
    bad: dict[str, Any] = {
        "emotion_max_length": 1,
        "emotion_batch_size": 0,
        "emotion_cache_size": -1,
        "emotion_failure_threshold": 0,
        "emotion_cooldown_seconds": -5.0,
        "emotion_slow_ms": 0.0,
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: bad[field]})


def test_day_11_chat_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.chat_max_message_chars == 4000
    assert settings.chat_rate_limit_per_minute == 30
    assert settings.chat_history_turns == 10
    assert settings.chat_ephemeral_ttl_seconds == 30 * 60, "the brief: TTL 30 minutes"
    assert settings.chat_ephemeral_max_sessions == 5000
    assert settings.chat_ephemeral_max_turns == 60


def test_chat_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_RATE_LIMIT_PER_MINUTE", "7")
    monkeypatch.setenv("CHAT_EPHEMERAL_TTL_SECONDS", "60")
    settings = Settings(_env_file=None)
    assert settings.chat_rate_limit_per_minute == 7
    assert settings.chat_ephemeral_ttl_seconds == 60


@pytest.mark.parametrize(
    "bad",
    [
        {"chat_max_message_chars": 0},
        {"chat_rate_limit_per_minute": 0},
        {"chat_history_turns": -1},
        {"chat_ephemeral_ttl_seconds": 0},
        {"chat_ephemeral_max_sessions": 0},
        {"chat_ephemeral_max_turns": 1},
    ],
)
def test_absurd_chat_settings_are_refused_at_startup(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **bad)


# --------------------------------------------------------------------------- #
# Day 40: real-time voice settings                                              #
# --------------------------------------------------------------------------- #


def test_realtime_voice_uses_web_rtc_vad_defaults_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    assert settings.voice_realtime_enabled is True
    assert settings.voice_sample_rate_hz == 16_000
    assert settings.voice_vad_mode == 2
    assert settings.voice_vad_silence_ms == 500

    monkeypatch.setenv("VOICE_VAD_MODE", "3")
    monkeypatch.setenv("VOICE_SAMPLE_RATE_HZ", "48000")
    configured = Settings(_env_file=None)
    assert configured.voice_vad_mode == 3
    assert configured.voice_sample_rate_hz == 48_000


@pytest.mark.parametrize(
    "bad",
    [
        {"voice_sample_rate_hz": 24_000},
        {"voice_vad_mode": -1},
        {"voice_vad_mode": 4},
        {"voice_vad_silence_ms": 99},
        {"voice_silence_timeout_seconds": 4},
        {"voice_barge_in_target_ms": 1_001},
    ],
)
def test_unusable_realtime_voice_settings_are_refused_at_startup(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **bad)


# --------------------------------------------------------------------------- #
# Day 13: retrieval settings                                                    #
# --------------------------------------------------------------------------- #


def test_rag_settings_have_safe_defaults() -> None:
    from app.core.config import RAG_EMBEDDER_CHOICES
    from app.services.rag.embedders import EMBEDDER_KINDS

    settings = Settings(_env_file=None)
    assert settings.rag_enabled is True
    assert settings.rag_embedder == "auto"  # real model when available, tfidf offline
    assert settings.rag_embedding_model_id  # documented in .env.example, never hard-coded elsewhere
    assert settings.rag_embedding_dim >= 64
    assert settings.rag_persist_dir
    assert settings.rag_min_similarity == 0.0, "0 = defer to the per-embedder floor"
    assert 200 <= settings.rag_chunk_words <= 300, "the brief's chunk band"
    assert 0 < settings.rag_chunk_overlap_words < settings.rag_chunk_words
    assert 1 <= settings.rag_retrieval_limit <= 10
    assert set(RAG_EMBEDDER_CHOICES) == set(EMBEDDER_KINDS)


def test_rag_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ENABLED", "false")
    monkeypatch.setenv("RAG_EMBEDDER", "tfidf")
    monkeypatch.setenv("RAG_MIN_SIMILARITY", "0.2")
    monkeypatch.setenv("RAG_RETRIEVAL_LIMIT", "5")
    settings = Settings(_env_file=None)
    assert settings.rag_enabled is False
    assert settings.rag_embedder == "tfidf"
    assert settings.rag_min_similarity == 0.2
    assert settings.rag_retrieval_limit == 5


@pytest.mark.parametrize(
    "bad",
    [
        {"rag_embedder": "brainwaves"},
        {"rag_embedding_model_id": "   "},
        {"rag_embedding_dim": 8},
        {"rag_persist_dir": "  "},
        {"rag_min_similarity": 1.5},
        {"rag_min_similarity": -0.1},
        {"rag_chunk_words": 10},
        {"rag_chunk_words": 10_000},
        {"rag_chunk_overlap_words": 500, "rag_chunk_words": 220},
        {"rag_retrieval_limit": 0},
        {"rag_retrieval_limit": 11},
    ],
)
def test_absurd_rag_settings_are_refused_at_startup(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **bad)


def test_blank_embedding_model_only_rejected_for_model_kinds() -> None:
    # tfidf/hashing never touch the model, so a blank id is fine there.
    Settings(_env_file=None, rag_embedder="tfidf", rag_embedding_model_id="")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_embedder="sentence_transformers", rag_embedding_model_id="")


# --------------------------------------------------------------------------- #
# Day 35: RETRIEVER and the calibrated overrides around it                     #
# --------------------------------------------------------------------------- #


def test_retriever_defaults_to_the_day13_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    # A new retrieval implementation ships dark: the default is the retriever
    # that has been in production since Day 13, so upgrading the code changes
    # nothing until somebody asks for it.
    monkeypatch.delenv("RETRIEVER", raising=False)
    assert Settings(_env_file=None).retriever == "baseline"


def test_retriever_is_read_from_the_env_without_a_rag_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The brief names the key `RETRIEVER`, and the key is the contract: it
    # selects which retriever exists rather than tuning one.
    monkeypatch.setenv("RETRIEVER", "hybrid_rerank")
    assert Settings(_env_file=None).retriever == "hybrid_rerank"


def test_the_retriever_choices_are_the_three_the_brief_names() -> None:
    from app.core.config import RETRIEVER_CHOICES

    assert set(RETRIEVER_CHOICES) == {"baseline", "hybrid", "hybrid_rerank"}


@pytest.mark.parametrize(
    "bad",
    [
        {"retriever": "magic"},
        {"retriever": ""},
        {"rag_hybrid_dense_weight": 1.5},
        {"rag_hybrid_dense_weight": -0.1},
        {"rag_hybrid_fusion_k": 0},
        {"rag_hybrid_candidates": 0},
        {"rag_rerank_candidates": 0},
        {"rag_abstain_score": 1.5},
        {"rag_abstain_score": -0.01},
        {"rag_hybrid_candidates": 5, "rag_rerank_candidates": 9},
    ],
)
def test_absurd_retriever_settings_are_refused_at_startup(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **bad)


def test_the_calibrated_overrides_default_to_none_meaning_use_the_artifact() -> None:
    # `None` is not "0.5 by another route": it is "the fitted value wins", and a
    # number here means an operator is deliberately overriding a measurement.
    settings = Settings(_env_file=None)
    assert settings.rag_hybrid_dense_weight is None
    assert settings.rag_hybrid_fusion_k is None
    assert settings.rag_hybrid_candidates is None
    assert settings.rag_rerank_candidates is None
    assert settings.rag_abstain_score is None
    assert settings.rag_reranker_dir == "app/ml_artifacts/retrieval_v1"


def test_a_blank_retriever_key_in_the_example_env_boots() -> None:
    # Day 30's rule: `.env.example` ships every key blank, and a blank value
    # means "unset" rather than "" (which is not one of the three choices).
    monkey = Settings(_env_file=None, retriever="baseline")
    assert monkey.retriever == "baseline"
