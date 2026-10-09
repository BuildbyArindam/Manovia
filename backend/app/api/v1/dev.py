"""Development-only diagnostics; never registered in production."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.services.nlp.base import EmotionResult
from app.services.nlp.service import EmotionService

router = APIRouter(prefix="/dev", tags=["development"])


class AnalyzeRequest(BaseModel):
    text: str = Field(max_length=10000)
    lang: Literal["en", "hi", "bn", "other"] | None = None


@router.post("/analyze", response_model=EmotionResult)
def analyze(payload: AnalyzeRequest, request: Request) -> EmotionResult:
    service: EmotionService = request.app.state.emotion_service
    return service.analyze(payload.text, payload.lang)
