"""Day 45: the process-wide meditation service builder.

``build_meditation_service`` is the only place that decides whether LLM
passages are enabled at all and which TTS engine the renderer uses, so the
three configuration branches are pinned here: fake (offline default), a
configured real provider (LLM on), and the Piper local engine.
"""

from __future__ import annotations

from pathlib import Path

from app.api.v1.meditation import build_meditation_service
from app.core.config import Settings
from app.services.meditation.tts import MeditationTtsRenderer


def test_offline_default_is_fake_and_curated_only(settings: Settings, tmp_path: Path) -> None:
    service = build_meditation_service(settings)
    assert service.generator.llm_enabled is False
    assert isinstance(service.renderer, MeditationTtsRenderer)
    assert service.renderer.provider == "fake"


def test_configured_provider_enables_llm_passages(settings: Settings, tmp_path: Path) -> None:
    # Both a key and a model id are required for the provider to be
    # configured (is_configured checks both); the values are never used
    # here because no completion is ever requested.
    configured = settings.model_copy(
        update={
            "llm_provider": "anthropic",
            "anthropic_api_key": "sk-test-only",
            "anthropic_model": "claude-test",
        }
    )
    service = build_meditation_service(configured)
    assert service.generator.llm_enabled is True
    assert service.generator._provider is not None


def test_fake_provider_is_never_an_llm_source(settings: Settings, tmp_path: Path) -> None:
    fake = settings.model_copy(update={"llm_provider": "fake"})
    service = build_meditation_service(fake)
    assert service.generator.llm_enabled is False


def test_piper_engine_is_selected_when_configured(settings: Settings, tmp_path: Path) -> None:
    model = str(tmp_path / "en_US-lessac-medium.onnx")
    piper = settings.model_copy(
        update={"voice_tts_provider": "piper", "voice_piper_model_en": model}
    )
    service = build_meditation_service(piper)
    assert service.renderer.provider == "piper"
    assert service.renderer._piper_model == model


def test_cache_dir_defaults_outside_the_repo(settings: Settings, tmp_path: Path) -> None:
    blank = settings.model_copy(update={"meditation_tts_cache_dir": None})
    service = build_meditation_service(blank)
    # The default lives under the user's cache root, never in the working
    # directory or the repository.
    assert service.renderer._cache_dir is not None
    assert Path.home() in service.renderer._cache_dir.parents


def test_explicit_cache_dir_wins(settings: Settings, tmp_path: Path) -> None:
    explicit = settings.model_copy(update={"meditation_tts_cache_dir": str(tmp_path / "tts")})
    service = build_meditation_service(explicit)
    assert service.renderer._cache_dir == tmp_path / "tts"
