"""Day 45: voice rendering, content-hash cache, captions.

The renderer is a pure function of the script: same script + same
provider/model/speed => identical bytes (and a cache hit the second time).
The fake provider produces deterministic silent WAVs (the Day 39 offline
convention); the Piper path is structural (a missing model is an honest
error, never silence).
"""

from __future__ import annotations

import io
import json
import math
import wave

import pytest
import pytest_asyncio

from app.services.meditation.generate import MeditationScriptGenerator
from app.services.meditation.tts import (
    SAMPLE_RATE_HZ,
    MeditationTtsRenderer,
    TtsRenderError,
    _resample_s16le,
    cache_key,
    captions_for,
)
from app.services.meditation.types import MeditationRequest, MeditationScript


@pytest_asyncio.fixture
async def script() -> MeditationScript:
    generator = MeditationScriptGenerator()
    return await generator.generate(
        MeditationRequest(
            theme="calm",
            duration_seconds=180,
            language="en",
            context="seated",
            hour_of_day=12,
            seed=9,
            eyes_open=True,
        )
    )


@pytest_asyncio.fixture
async def other_script() -> MeditationScript:
    generator = MeditationScriptGenerator()
    return await generator.generate(
        MeditationRequest(
            theme="calm",
            duration_seconds=180,
            language="en",
            context="seated",
            hour_of_day=12,
            seed=10,
            eyes_open=True,
        )
    )


def _wav_duration(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def test_fake_render_is_valid_wav_with_estimated_duration(script: MeditationScript) -> None:
    renderer = MeditationTtsRenderer(provider="fake", piper_model=None, cache_dir=None)
    result = renderer.render(script)
    assert result.cache == "miss"
    assert result.wav_bytes[:4] == b"RIFF"
    assert result.wav_bytes[8:12] == b"WAVE"
    duration = _wav_duration(result.wav_bytes)
    # Close to the script's estimate (same speaking-rate model).
    assert abs(duration - script.estimated_duration_s) < 2.0
    assert result.duration_seconds == pytest.approx(duration, abs=0.01)
    assert result.transcript == script.transcript
    assert result.provider == "fake"


def test_captions_are_one_per_section_with_script_timing(script: MeditationScript) -> None:
    renderer = MeditationTtsRenderer(provider="fake", piper_model=None, cache_dir=None)
    result = renderer.render(script)
    assert len(result.captions) == len(script.sections)
    for caption, section in zip(result.captions, script.sections, strict=True):
        assert caption.section_id == section.id
        assert caption.start_s == section.start_s
        assert caption.end_s == section.end_s
        assert caption.text == section.text
    assert result.captions[0].start_s == 0.0
    # Pauses: each caption after the first starts at or after the previous end.
    for earlier, later in zip(result.captions, result.captions[1:], strict=False):
        assert later.start_s >= earlier.end_s


def test_render_is_cached_by_content_hash(script: MeditationScript, tmp_path) -> None:
    renderer = MeditationTtsRenderer(provider="fake", piper_model=None, cache_dir=tmp_path)
    first = renderer.render(script)
    second = renderer.render(script)
    assert first.cache == "miss"
    assert second.cache == "hit"
    assert first.wav_bytes == second.wav_bytes
    assert first.captions == second.captions
    # Both cache files exist under the render's content hash.
    wav_file = tmp_path / f"{first.content_hash}.render.wav"
    meta_file = tmp_path / f"{first.content_hash}.render.json"
    assert wav_file.is_file()
    assert meta_file.is_file()
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["content_hash"] == first.content_hash
    assert meta["transcript"] == script.transcript
    assert len(meta["captions"]) == len(script.sections)


def test_cache_key_is_stable_and_input_sensitive() -> None:
    hash_a = "a" * 64
    hash_b = "b" * 64
    same = cache_key(provider="fake", model_name="none", content_hash=hash_a)
    again = cache_key(provider="fake", model_name="none", content_hash=hash_a)
    assert same == again
    # The content hash is a function of language, speed and every section,
    # so different script content shows up as a different hash in the key.
    assert cache_key(provider="fake", model_name="none", content_hash=hash_b) != same
    assert cache_key(provider="piper", model_name="none", content_hash=hash_a) != same
    assert cache_key(provider="fake", model_name="x.onnx", content_hash=hash_a) != same


def test_tampered_sections_change_the_content_hash(script: MeditationScript) -> None:
    """The Day 45 verification regression: the renderer reads the sections,
    so any tamper with a section's text (or role, source, pause) must break
    the hash — the transcript field alone is display-only."""
    from app.services.meditation.generate import _content_hash

    original = _content_hash(
        language=script.language,
        theme=script.resolved_theme,
        duration_seconds=script.duration_seconds,
        voice_speed=script.voice_speed,
        context=script.context,
        eyes_open=script.eyes_open,
        preferences=script.preferences,
        seed=script.seed,
        sections=script.sections,
    )
    assert original == script.content_hash
    tampered = [section.model_copy() for section in script.sections]
    tampered[0] = tampered[0].model_copy(update={"text": "tampered text"})
    assert (
        _content_hash(
            language=script.language,
            theme=script.resolved_theme,
            duration_seconds=script.duration_seconds,
            voice_speed=script.voice_speed,
            context=script.context,
            eyes_open=script.eyes_open,
            preferences=script.preferences,
            seed=script.seed,
            sections=tampered,
        )
        != script.content_hash
    )


def test_corrupt_cache_entry_is_a_miss_not_an_error(
    script: MeditationScript, tmp_path: Path
) -> None:
    renderer = MeditationTtsRenderer(provider="fake", piper_model=None, cache_dir=tmp_path)
    first = renderer.render(script)
    meta_file = tmp_path / f"{first.content_hash}.render.json"
    meta_file.write_text("{not json", encoding="utf-8")
    result = renderer.render(script)
    assert result.cache == "miss"
    assert result.wav_bytes[:4] == b"RIFF"


async def test_different_scripts_do_not_share_cache(
    script: MeditationScript, other_script: MeditationScript, tmp_path
) -> None:
    renderer = MeditationTtsRenderer(provider="fake", piper_model=None, cache_dir=tmp_path)
    first = renderer.render(script)
    second = renderer.render(other_script)
    assert second.cache == "miss"
    assert second.wav_bytes != first.wav_bytes
    assert first.content_hash != second.content_hash


def test_unknown_provider_rejected() -> None:
    with pytest.raises(TtsRenderError, match="unknown TTS provider"):
        MeditationTtsRenderer(provider="espeak", piper_model=None, cache_dir=None)


async def test_piper_missing_model_is_an_error(script: MeditationScript, tmp_path) -> None:
    renderer = MeditationTtsRenderer(
        provider="piper", piper_model=str(tmp_path / "missing.onnx"), cache_dir=None
    )
    with pytest.raises(TtsRenderError, match="Piper model is absent"):
        renderer.render(script)


def test_cache_disabled_renders_but_never_caches(script: MeditationScript) -> None:
    renderer = MeditationTtsRenderer(provider="fake", piper_model=None, cache_dir=None)
    for _ in range(2):
        result = renderer.render(script)
        assert result.cache == "miss"
    assert result.transcript == script.transcript


def test_resample_keeps_the_signal() -> None:
    # A 100 Hz sine resampled at the same rate is unchanged (mono S16LE).
    src = bytearray()
    for i in range(SAMPLE_RATE_HZ):
        value = int(10000 * math.sin(2 * math.pi * 100 * i / SAMPLE_RATE_HZ))
        src += value.to_bytes(2, "little", signed=True)
    assert _resample_s16le(bytes(src), SAMPLE_RATE_HZ, SAMPLE_RATE_HZ) == bytes(src)
    # Halving the rate halves the frame count.
    out = _resample_s16le(bytes(src), 2 * SAMPLE_RATE_HZ, SAMPLE_RATE_HZ)
    assert len(out) == len(src) // 2


def test_captions_for_is_a_pure_function(script: MeditationScript) -> None:
    first = captions_for(script)
    second = captions_for(script)
    assert first == second
    assert [c.index for c in first] == list(range(len(script.sections)))
