from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings

router = APIRouter(tags=["voice"])


class TranscriptionResponse(BaseModel):
    text: str
    language: str
    language_probability: float


@router.post("/voice/transcribe", response_model=TranscriptionResponse)
async def transcribe(
    audio: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    user: Any = Depends(get_current_user),
) -> TranscriptionResponse:
    from app.services.voice.stt import transcribe_audio

    return TranscriptionResponse(**transcribe_audio(audio))


@router.get("/voice/voices")
async def get_voices(user: Any = Depends(get_current_user)) -> dict[str, Any]:
    # Report what voices are theoretically available.
    return {
        "voices": [
            {"id": "en_US", "language": "en"},
            {"id": "hi_IN", "language": "hi"},
            {"id": "bn_IN", "language": "bn"},
        ]
    }


@router.post("/voice/synthesize")
async def synthesize(
    text: str = Form(...),
    language: str = Form("en"),
    speed: float = Form(1.0),
    user: Any = Depends(get_current_user),
) -> Response:
    from app.services.voice.stt import generate_speech

    audio_bytes = generate_speech(text, language, speed)
    return Response(content=audio_bytes, media_type="audio/wav")
