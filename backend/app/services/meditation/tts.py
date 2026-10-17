"""Day 45: voice rendering for meditation scripts.

Rendering walks the script section by section — the same structure the
pause markers use — and concatenates per-section PCM with the declared
silence gaps. That is the "SSML-like pause" mechanism: the local Piper
binary takes plain text (it does not parse SSML), so the pauses live in
the composition, not in the synthesiser.

* ``fake`` provider: deterministic silent WAV sized to the estimated
  duration (offline tests, the Day 39 convention).
* ``piper`` provider: one local subprocess per section, operator-supplied
  model paths, no network. A missing model is an honest 503, not silence.

The render is cached by content hash (provider + language + speed + model
+ transcript). A hit reads the WAV and captions back from the cache;
nothing is re-synthesised. Cache writes are best-effort: a full disk must
degrade to a cache miss, never to a failed request. Audio and captions are
written under the cache directory only — never into the repository, never
into the database, never to a log.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.services.meditation.types import CaptionOut, MeditationScript
from app.services.voice.stt import _piper_sample_rate

SAMPLE_RATE_HZ: Final = 22_050
_CACHE_FILE_SUFFIX = "render"


class TtsRenderError(RuntimeError):
    """Local TTS is unavailable for this render. Carries no script text."""


@dataclass(frozen=True)
class RenderResult:
    wav_bytes: bytes
    duration_seconds: float
    captions: tuple[CaptionOut, ...]
    transcript: str
    content_hash: str
    cache: str  # "hit" | "miss"
    provider: str


def cache_key(*, provider: str, model_name: str, content_hash: str) -> str:
    """The key of the render cache.

    Two renders are interchangeable only when the engine, the voice and
    the script's content hash are identical. The content hash already
    covers everything that changes the words or their timing (language,
    speed, every section), so nothing else belongs in the key.
    """
    basis = "|".join([provider, model_name, content_hash])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _words(text: str) -> int:
    return len(text.split())


def _section_duration_s(text: str, voice_speed: float, words_per_minute: float) -> float:
    wps = max(words_per_minute * voice_speed / 60.0, 0.1)
    return max(_words(text) / wps, 0.5)


def captions_for(script: MeditationScript) -> tuple[CaptionOut, ...]:
    """Per-section captions with the script's own estimated timing.

    Timing is *estimated* from the speaking-rate model (the script already
    carries section start/end); a real Piper render differs by a few
    seconds and the caller says so (``timing_basis="estimated"``).
    """
    captions: list[CaptionOut] = []
    for index, section in enumerate(script.sections):
        captions.append(
            CaptionOut(
                index=index,
                section_id=section.id,
                start_s=section.start_s,
                end_s=section.end_s,
                text=section.text,
            )
        )
    return tuple(captions)


def _silent_pcm(duration_s: float, sample_rate_hz: int) -> bytes:
    return b"\x00" * (int(duration_s * sample_rate_hz) * 2)


def _synth_section_piper(text: str, model_path: str) -> tuple[bytes, int]:
    """One section through the local Piper binary. Returns PCM + rate."""
    path = Path(model_path)
    if not path.is_file():
        raise TtsRenderError("configured Piper model is absent")
    try:
        completed = subprocess.run(
            ["piper", "--model", str(path), "--output_raw"],
            input=text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TtsRenderError("local Piper synthesis failed") from exc
    if not completed.stdout:
        raise TtsRenderError("local Piper returned no audio")
    return completed.stdout, _piper_sample_rate(path)


def _resample_s16le(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear resample of mono S16LE. Meditation audio is gentle; linear is
    plenty, and it keeps the concatenation to one clock."""
    if src_rate == dst_rate:
        return pcm
    src_frames = len(pcm) // 2
    dst_frames = int(src_frames * dst_rate / src_rate)
    out = bytearray()
    for i in range(dst_frames):
        pos = i * (src_rate / dst_rate)
        lo = int(pos)
        hi = min(lo + 1, src_frames - 1)
        frac = pos - lo
        a = int.from_bytes(pcm[lo * 2 : lo * 2 + 2], "little", signed=True)
        b = int.from_bytes(pcm[hi * 2 : hi * 2 + 2], "little", signed=True)
        value = int(a + (b - a) * frac)
        value = max(-32768, min(32767, value))
        out += value.to_bytes(2, "little", signed=True)
    return bytes(out)


def _pcm_to_wav_bytes(pcm: bytes, sample_rate_hz: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(pcm)
    return output.getvalue()


class MeditationTtsRenderer:
    """Section-wise local rendering with a content-hash cache."""

    def __init__(self, *, provider: str, piper_model: str | None, cache_dir: Path | None) -> None:
        if provider not in {"fake", "piper"}:
            raise TtsRenderError("unknown TTS provider")
        self._provider = provider
        self._piper_model = piper_model
        self._cache_dir = cache_dir

    @property
    def provider(self) -> str:
        return self._provider

    def _model_name(self) -> str:
        if self._provider != "piper" or not self._piper_model:
            return "none"
        return Path(self._piper_model).name

    def _cache_paths(self, key: str) -> tuple[Path, Path]:
        if self._cache_dir is None:
            raise TtsRenderError("TTS cache disabled")
        return (
            self._cache_dir / f"{key}.{_CACHE_FILE_SUFFIX}.wav",
            self._cache_dir / f"{key}.{_CACHE_FILE_SUFFIX}.json",
        )

    def render(self, script: MeditationScript, *, words_per_minute: float = 115.0) -> RenderResult:
        key = cache_key(
            provider=self._provider,
            model_name=self._model_name(),
            content_hash=script.content_hash,
        )
        if self._cache_dir is not None:
            wav_path, meta_path = self._cache_paths(key)
            if wav_path.is_file() and meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    return RenderResult(
                        wav_bytes=wav_path.read_bytes(),
                        duration_seconds=float(meta["duration_seconds"]),
                        captions=tuple(CaptionOut(**item) for item in meta["captions"]),
                        transcript=str(meta["transcript"]),
                        content_hash=str(meta["content_hash"]),
                        cache="hit",
                        provider=self._provider,
                    )
                except (OSError, ValueError, KeyError, TypeError):
                    # Corrupt cache entry: fall through and re-render.
                    pass

        result = self._render_fresh(script, key, words_per_minute)
        if self._cache_dir is not None:
            self._write_cache(key, result)
        return result

    def _render_fresh(
        self, script: MeditationScript, key: str, words_per_minute: float
    ) -> RenderResult:
        captions = captions_for(script)
        pcm_parts: list[bytes] = []
        rate = SAMPLE_RATE_HZ
        for section in script.sections:
            if section.pause_before_ms:
                pcm_parts.append(_silent_pcm(section.pause_before_ms / 1000.0, rate))
            if self._provider == "fake":
                duration = _section_duration_s(section.text, script.voice_speed, words_per_minute)
                pcm_parts.append(_silent_pcm(duration, rate))
            else:
                assert self._piper_model is not None
                section_pcm, section_rate = _synth_section_piper(section.text, self._piper_model)
                rate = section_rate
                pcm_parts.append(_resample_s16le(section_pcm, section_rate, SAMPLE_RATE_HZ))
        pcm = b"".join(pcm_parts)
        wav = _pcm_to_wav_bytes(pcm, SAMPLE_RATE_HZ)
        duration = len(pcm) / 2 / SAMPLE_RATE_HZ
        return RenderResult(
            wav_bytes=wav,
            duration_seconds=round(duration, 2),
            captions=captions,
            transcript=script.transcript,
            content_hash=key,
            cache="miss",
            provider=self._provider,
        )

    def _write_cache(self, key: str, result: RenderResult) -> None:
        # Best-effort: a full or read-only cache dir is a miss next time,
        # never an error this time.
        if self._cache_dir is None:
            return
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            meta = {
                "content_hash": result.content_hash,
                "duration_seconds": result.duration_seconds,
                "transcript": result.transcript,
                "captions": [c.model_dump() for c in result.captions],
            }
            (self._cache_dir / f"{key}.{_CACHE_FILE_SUFFIX}.wav").write_bytes(result.wav_bytes)
            (self._cache_dir / f"{key}.{_CACHE_FILE_SUFFIX}.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            return


def piper_model_for(language: str, paths: dict[str, str | None]) -> str | None:
    """Mirror of the Day 39 model selection: the language's model, else en."""
    return paths.get(language.split("-")[0]) or paths.get("en")


__all__ = [
    "SAMPLE_RATE_HZ",
    "MeditationTtsRenderer",
    "RenderResult",
    "TtsRenderError",
    "cache_key",
    "captions_for",
    "piper_model_for",
]
