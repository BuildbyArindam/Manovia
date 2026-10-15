"""Local-only STT/TTS adapters for the voice surfaces.

The Day 39 upload endpoint retains its compatibility adapter below.  Day 40
uses the in-memory incremental class: it constructs a WAV in ``BytesIO`` for a
local Faster-Whisper model and never creates a temporary audio file.  Neither
adapter analyses emotion/health/speaker traits — recognisers produce words only.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.core.config import Settings


class SpeechSynthesisError(RuntimeError):
    """A local TTS provider/model is unavailable.  It carries no text."""


class LocalTranscriptionError(RuntimeError):
    """A local STT provider/model is unavailable.  It carries no audio/text."""


def transcribe_audio(audio_file: UploadFile) -> dict[str, Any]:
    """Compatibility path for Day 39 uploaded clips.

    This path is intentionally separate from real-time voice.  Its temporary
    file is unlinked in ``finally`` before returning; Day 40 PCM chunks use the
    in-memory incremental adapter below and never enter this function.
    """
    from app.core.config import get_settings

    get_settings()
    model_size = os.getenv("VOICE_STT_MODEL", "tiny")

    if os.getenv("VOICE_STT_FAKE", "false").lower() == "true":
        return {
            "text": "This is a fake transcription of short audio.",
            "language": "en",
            "language_probability": 0.99,
        }

    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        download_root=os.getenv("VOICE_MODEL_DIR", "/tmp/whisper"),
    )

    fd, temp_path = tempfile.mkstemp(suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(audio_file.file.read())
        segments, info = model.transcribe(temp_path, beam_size=5)
        text = " ".join(segment.text for segment in segments)
    finally:
        os.unlink(temp_path)

    return {
        "text": text.strip(),
        "language": info.language,
        "language_probability": info.language_probability,
    }


class InMemoryWhisperTranscriber:
    """Repeatedly transcribe a growing PCM buffer without ever writing a file.

    Faster-Whisper itself is not a token-streaming recogniser.  Sampling its
    local decoder every configured interval gives the UI a provisional text
    update while speech continues, then ``finish`` produces the final text.
    Audio is held only for one utterance and ``discard`` wipes the buffer.
    """

    def __init__(
        self,
        *,
        model_loader: Callable[[], Any],
        language: str,
        sample_rate_hz: int,
        partial_interval_ms: int,
    ) -> None:
        self._model_loader = model_loader
        self._language = language if language in {"en", "hi", "bn"} else "en"
        self._sample_rate_hz = sample_rate_hz
        self._partial_interval_bytes = max(
            2, int(sample_rate_hz * 2 * partial_interval_ms / 1000.0)
        )
        self._pcm = bytearray()
        self._last_partial_size = 0

    async def push(self, pcm16le: bytes) -> str | None:
        self._pcm.extend(pcm16le)
        if len(self._pcm) - self._last_partial_size < self._partial_interval_bytes:
            return None
        self._last_partial_size = len(self._pcm)
        return await asyncio.to_thread(self._decode)

    async def finish(self) -> str:
        if not self._pcm:
            raise LocalTranscriptionError("empty utterance")
        return await asyncio.to_thread(self._decode)

    def discard(self) -> None:
        # bytearray clearing is the strongest practical in-process lifecycle
        # control in Python.  There is no filename, cache key, DB row or log.
        self._pcm.clear()
        self._last_partial_size = 0

    def _decode(self) -> str:
        try:
            model = self._model_loader()
            wav = _pcm_to_wav(bytes(self._pcm), self._sample_rate_hz)
            segments, _ = model.transcribe(
                wav,
                language=self._language,
                beam_size=1,
                vad_filter=False,
            )
            return " ".join(str(segment.text).strip() for segment in segments).strip()
        except Exception as exc:
            raise LocalTranscriptionError("local recogniser unavailable") from exc


class WhisperTranscriberFactory:
    """Lazily load one optional local Faster-Whisper model per process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Any | None = None

    def __call__(self, language: str, sample_rate_hz: int) -> InMemoryWhisperTranscriber:
        return InMemoryWhisperTranscriber(
            model_loader=self._model_or_raise,
            language=language,
            sample_rate_hz=sample_rate_hz,
            partial_interval_ms=self._settings.voice_stt_partial_interval_ms,
        )

    def _model_or_raise(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                os.getenv("VOICE_STT_MODEL", "tiny"),
                device="cpu",
                compute_type="int8",
                # An operator may point this at a pre-downloaded local model.
                # No runtime download is initiated by this code.
                local_files_only=True,
                download_root=os.getenv("VOICE_MODEL_DIR", "/tmp/whisper"),
            )
            return self._model
        except Exception as exc:
            raise LocalTranscriptionError("local recogniser unavailable") from exc


def build_incremental_transcriber_factory(settings: Settings) -> WhisperTranscriberFactory:
    """Build the default local-only real-time STT factory."""
    return WhisperTranscriberFactory(settings)


def generate_speech(
    text: str,
    language: str = "en",
    speed: float = 1.0,
    *,
    provider: str | None = None,
    piper_model_paths: dict[str, str | None] | None = None,
) -> bytes:
    """Synthesize assistant text locally.

    ``fake`` makes a deterministic WAV for offline tests.  ``piper`` invokes a
    locally installed Piper binary and an operator-supplied local model path;
    it makes no network call and writes neither the words nor audio to a file.
    ``speed`` remains for the Day 39 HTTP contract; Piper's voice configuration
    owns real speaking-rate changes.
    """
    del speed  # compatibility-only; Piper voice settings control actual rate.
    selected = provider or os.getenv("VOICE_TTS_PROVIDER", "fake")
    if selected == "fake":
        return _silent_wav()
    if selected != "piper":
        raise SpeechSynthesisError("unknown local TTS provider")
    paths = piper_model_paths or {
        "en": os.getenv("VOICE_PIPER_MODEL_EN"),
        "hi": os.getenv("VOICE_PIPER_MODEL_HI"),
        "bn": os.getenv("VOICE_PIPER_MODEL_BN"),
    }
    model_path = paths.get(language.split("-")[0]) or paths.get("en")
    if not model_path:
        raise SpeechSynthesisError("no local Piper model configured")
    return _piper_wav(text, Path(model_path))


def _pcm_to_wav(pcm16le: bytes, sample_rate_hz: int) -> io.BytesIO:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(pcm16le)
    output.seek(0)
    return output


def _silent_wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22_050)
        # 100 ms deterministic test fixture, no spoken content.
        wav.writeframes(b"\x00" * (int(22_050 * 0.1) * 2))
    return output.getvalue()


def _piper_wav(text: str, model_path: Path) -> bytes:
    if not model_path.is_file():
        raise SpeechSynthesisError("configured Piper model is absent")
    sample_rate_hz = _piper_sample_rate(model_path)
    try:
        completed = subprocess.run(
            ["piper", "--model", str(model_path), "--output_raw"],
            input=text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SpeechSynthesisError("local Piper synthesis failed") from exc
    if not completed.stdout:
        raise SpeechSynthesisError("local Piper returned no audio")
    return _pcm_to_wav(completed.stdout, sample_rate_hz).getvalue()


def _piper_sample_rate(model_path: Path) -> int:
    config_path = Path(f"{model_path}.json")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        value = payload.get("audio", {}).get("sample_rate")
        if isinstance(value, int) and value > 0:
            return value
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    # Piper's common model rate.  The operator guide tells deployers to retain
    # the adjacent .onnx.json so an unusual voice's declared rate wins.
    return 22_050
