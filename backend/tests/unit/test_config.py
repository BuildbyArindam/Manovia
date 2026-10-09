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
    monkeypatch.setenv("EMOTION_ANALYZER", "keyword")
    monkeypatch.setenv("EMOTION_MODEL_ID", "")
    settings = Settings(_env_file=None)
    assert settings.emotion_analyzer == "keyword"
    assert settings.emotion_model_id == ""


@pytest.mark.parametrize("choice", ["auto", "hf", "keyword", "sentiment", "fake"])
def test_every_documented_analyzer_choice_is_accepted(choice: str) -> None:
    settings = Settings(_env_file=None, emotion_analyzer=choice)
    assert settings.emotion_analyzer == choice


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
