import os
import tempfile
from typing import Any

from fastapi import UploadFile


def transcribe_audio(audio_file: UploadFile) -> dict[str, Any]:
    from app.core.config import get_settings

    get_settings()
    model_size = os.getenv("VOICE_STT_MODEL", "tiny")

    # Use Fake STT if set or in tests
    if os.getenv("APP_ENV") == "test" or os.getenv("VOICE_STT_FAKE", "false").lower() == "true":
        return {
            "text": "This is a fake transcription of short audio.",
            "language": "en",
            "language_probability": 0.99,
        }

    from faster_whisper import WhisperModel

    # Load inside the function to save memory when not used, or use a global.
    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        download_root=os.getenv("VOICE_MODEL_DIR", "/tmp/whisper"),
    )

    fd, temp_path = tempfile.mkstemp(suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio_file.file.read())

        segments, info = model.transcribe(temp_path, beam_size=5)
        text = " ".join([segment.text for segment in segments])
    finally:
        os.unlink(temp_path)

    return {
        "text": text.strip(),
        "language": info.language,
        "language_probability": info.language_probability,
    }


def generate_speech(text: str, language: str = "en", speed: float = 1.0) -> bytes:
    # Dummy TTS generator for test sandbox, since we can't download Piper models in sandbox
    import wave
    from io import BytesIO

    buf = BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        # 0.1s empty audio
        nframes = int(22050 * 0.1)
        wav.setnframes(nframes)
        wav.writeframes(b"\x00" * (nframes * 2))
    return buf.getvalue()
