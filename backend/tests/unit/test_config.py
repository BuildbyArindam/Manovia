"""Unit tests for application settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_defaults_load_in_development_without_secret_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("APP_ENV", "SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.app_env == "development"
    assert settings.secret_key is None


def test_production_without_secret_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(_env_file=None)


def test_production_with_secret_key_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "super-secret-value")
    settings = Settings(_env_file=None)
    assert settings.app_env == "production"
    assert settings.secret_key == "super-secret-value"


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
    env_file.write_text("APP_ENV=production\nSECRET_KEY=from-dotenv-file\n")
    settings = Settings(_env_file=env_file)
    assert settings.app_env == "production"
    assert settings.secret_key == "from-dotenv-file"


def test_get_settings_caches_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    first = get_settings()
    monkeypatch.setenv("APP_ENV", "development")
    assert get_settings() is first  # cached: later env changes are not picked up
    get_settings.cache_clear()
    assert get_settings().app_env == "development"
