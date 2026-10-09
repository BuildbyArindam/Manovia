"""Development-only NLP inspection endpoint.

``POST /api/v1/dev/analyze`` runs one string through the emotion chain and
returns exactly what the chain decided - including *which* analyzer answered,
whether the result came from the cache, and whether the input was truncated.
That is the point: when the model is unavailable the product must degrade
quietly, and this endpoint is how a developer finds out that it did.

It is mounted **only** when ``APP_ENV != production`` (see
:func:`app.main.create_app`), and it re-checks the setting on every request, so
a mis-set environment cannot expose it: in production the route does not exist
(404), it is absent from the OpenAPI schema, and the guard would raise 404
anyway.

Privacy (AGENTS.md rule 5): the text is analyzed and returned to the caller,
but it is never logged. The log line carries a short SHA-256 fingerprint and a
length instead, which is enough to correlate two lines about the same message
without being able to read it. Nothing here is stored - no database, no row.
"""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core.errors import ApiError
from app.services.nlp.base import EmotionResult
from app.services.nlp.cache import text_fingerprint
from app.services.nlp.language import LanguageInfo, detect_language

router = APIRouter(prefix="/dev", tags=["dev"])

#: Hard cap on one request. 10,000 characters (the verification case) must
#: pass; a megabyte paste should not reach a CPU tokenizer.
MAX_TEXT_LENGTH = 20_000


class AnalyzeRequest(BaseModel):
    """One string to analyze, plus an optional language hint."""

    text: str = Field(default="", max_length=MAX_TEXT_LENGTH)
    lang: str | None = Field(default=None, max_length=16)


class LanguagePayload(BaseModel):
    """What language detection concluded."""

    lang: str
    confidence: float
    hinglish: bool
    script: str


class AnalyzeResponse(BaseModel):
    """The emotion result plus the provenance a developer needs."""

    primary: str
    scores: dict[str, float]
    valence: float
    arousal: float
    confidence: float
    analyzer: str
    model: str | None
    language: LanguagePayload
    truncated: bool
    cached: bool
    duration_ms: float


def _require_dev(request: Request) -> None:
    settings = request.app.state.settings
    if settings.is_production:
        # Deliberately the generic 404: a production deployment must not
        # confirm that a dev route exists.
        raise ApiError(404, "not_found", "Not found")


def _payload(result: EmotionResult, language: LanguageInfo, duration_ms: float) -> AnalyzeResponse:
    return AnalyzeResponse(
        primary=result.primary,
        scores=result.scores,
        valence=result.valence,
        arousal=result.arousal,
        confidence=result.confidence,
        analyzer=result.analyzer,
        model=result.model,
        language=LanguagePayload(
            lang=language.lang,
            confidence=language.confidence,
            hinglish=language.hinglish,
            script=language.script,
        ),
        truncated=result.truncated,
        cached=result.cached,
        duration_ms=round(duration_ms, 3),
    )


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(payload: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    """Analyze one string with the configured emotion chain.

    The analyzer is synchronous and CPU-bound, so it runs in a worker thread;
    an unresponsive model must not block the event loop for every other request.
    """
    _require_dev(request)
    analyzer = request.app.state.emotion_analyzer

    language = detect_language(payload.text)
    lang = (payload.lang or "").strip() or language.lang

    started = time.perf_counter()
    result = await run_in_threadpool(analyzer.analyze, payload.text, lang)
    duration_ms = (time.perf_counter() - started) * 1000.0

    # Fingerprint and length only - never the text (AGENTS.md rule 5).
    structlog.get_logger().info(
        "emotion_analyzed",
        text_sha=text_fingerprint(payload.text),
        text_length=len(payload.text),
        lang=language.lang,
        hinglish=language.hinglish,
        primary=result.primary,
        valence=result.valence,
        arousal=result.arousal,
        analyzer=result.analyzer,
        cached=result.cached,
        truncated=result.truncated,
        duration_ms=round(duration_ms, 3),
    )
    return _payload(result, language, duration_ms)


@router.get("/analyze/state")
async def analyzer_state(request: Request) -> dict[str, object]:
    """Chain health, cache counters and the detected model. No text, no keys."""
    _require_dev(request)
    analyzer = request.app.state.emotion_analyzer
    describe = getattr(analyzer, "describe", None)
    if callable(describe):
        state: dict[str, object] = dict(describe())
    else:
        state = {"analyzer": analyzer.name, "model": analyzer.model_id}
    state["loaded"] = bool(getattr(getattr(analyzer, "primary", analyzer), "is_loaded", False))
    return state
