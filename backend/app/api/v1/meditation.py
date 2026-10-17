"""Day 45: the meditation surface.

* ``GET  /meditation/themes`` — the curated catalogue (unauthenticated,
  like the exercise list: it is reviewed content, not personal data).
* ``POST /meditation/scripts`` — compose one script. The response is the
  whole script (sections with pause markers and estimated timing, the
  transcript, the content hash, the safety booleans). No audio, no
  persistence: the client holds it and sends it back for rendering.
* ``POST /meditation/audio`` — render the client's script locally
  (Piper or the offline fake), section by section, with the pause
  markers as silence. Returns WAV bytes; the content hash and cache
  state travel in headers, never in the body.
* ``POST /meditation/audio/captions`` — the transcript and per-section
  captions for the same script (estimated timing, said plainly).

Logging is metadata-only (theme, language, duration, hash, provider,
cache state) per AGENTS.md rule 5: script text never reaches a log line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from structlog import get_logger

from app.api.deps import CurrentUser
from app.core.config import Settings
from app.core.errors import ApiError
from app.services.meditation.content import ContentError, load_themes
from app.services.meditation.generate import (
    MeditationCompositionError,
    MeditationScriptGenerator,
)
from app.services.meditation.tts import MeditationTtsRenderer, TtsRenderError
from app.services.meditation.types import (
    WORDS_PER_MINUTE_BASE,
    AudioRequest,
    CaptionsOut,
    MeditationRequest,
    MeditationScript,
    ThemeOut,
    ThemesOut,
)

router = APIRouter(prefix="/meditation", tags=["meditation"])
log = get_logger()


class MeditationService:
    """Process-wide generator + renderer pair (like the voice runtime)."""

    def __init__(
        self,
        generator: MeditationScriptGenerator,
        renderer: MeditationTtsRenderer,
    ) -> None:
        self.generator = generator
        self.renderer = renderer


def build_meditation_service(settings: Settings) -> MeditationService:
    """Build the service for this deployment.

    LLM passages are enabled only when a *configured* provider exists that
    is not the offline Fake: the Fake's canned reply is a test double, not
    a meditation voice, and offline scripts are curated-only by design.
    The renderer uses the Day 39/40 local TTS provider setting.
    """
    from app.services.llm import build_llm_chain

    provider = None
    allow_llm = False
    try:
        chain = build_llm_chain(settings)
        description = chain.describe()
        # The wrapper describes itself as "chain"; the primary provider is
        # the first entry of the "chain" list. LLM passages are enabled only
        # when that primary is configured AND is a real model: the Fake's
        # canned reply and the terminal canned fallback are test doubles,
        # not meditation voices.
        entries = description.get("chain")
        primary = entries[0] if isinstance(entries, list) and entries else {}
        if (
            description.get("configured")
            and primary.get("configured")
            and primary.get("provider") not in {"fake", "canned"}
        ):
            provider = chain
            allow_llm = True
    except Exception:  # pragma: no cover - the chain never raises for config
        provider = None

    cache_dir = (
        Path(settings.meditation_tts_cache_dir).expanduser()
        if settings.meditation_tts_cache_dir
        else Path.home() / ".cache" / "manovia" / "meditation-tts"
    )
    piper_model = None
    if settings.voice_tts_provider == "piper":
        piper_model = (
            settings.voice_piper_model_en
            or settings.voice_piper_model_hi
            or settings.voice_piper_model_bn
        )
    renderer = MeditationTtsRenderer(
        provider=settings.voice_tts_provider,
        piper_model=piper_model,
        cache_dir=cache_dir,
    )
    return MeditationService(
        generator=MeditationScriptGenerator(provider, allow_llm_passages=allow_llm),
        renderer=renderer,
    )


def get_meditation_service(request: Request) -> MeditationService:
    service: object = request.app.state.meditation_service
    if not isinstance(service, MeditationService):
        raise ApiError(503, "meditation_unavailable", "Meditations are not available right now.")
    return service


ServiceDep = Annotated[MeditationService, Depends(get_meditation_service)]


def _themes_out() -> ThemesOut:
    try:
        themes = [
            ThemeOut(
                id=t.id,
                label=t.label["en"],
                hint=t.hint["en"],
                best_time=t.best_time,
            )
            for t in load_themes()
        ]
    except ContentError as err:
        raise ApiError(
            503, "meditation_catalog_unavailable", "Meditations are not available right now."
        ) from err
    from app.services.meditation.types import PREFERENCE_TAGS

    return ThemesOut(themes=themes, preference_tags=sorted(PREFERENCE_TAGS))


@router.get("/themes", response_model=ThemesOut)
async def list_themes() -> ThemesOut:
    """The reviewed catalogue (public, like the exercise list: reviewed
    content, not personal data). Labels are English, the content's review
    language; the UI localises its own chrome."""
    return _themes_out()


@router.post("/scripts", response_model=MeditationScript)
async def create_script(
    request: Request,
    user: CurrentUser,
    body: MeditationRequest,
    service: ServiceDep,
) -> MeditationScript:
    """Compose one personalised script. Deterministic per request + seed."""
    del request, user
    try:
        script = await service.generator.generate(body)
    except MeditationCompositionError as err:
        log.warning(
            "meditation_composition_failed",
            theme=body.theme,
            language=body.language,
            duration_seconds=body.duration_seconds,
        )
        raise ApiError(
            503,
            "meditation_unavailable",
            "This one could not be composed safely; please try again.",
        ) from err
    log.info(
        "meditation_script_composed",
        theme=script.resolved_theme,
        language=script.language,
        duration_seconds=script.duration_seconds,
        word_count=script.word_count,
        llm_passages=sum(1 for s in script.sections if s.source == "llm"),
        content_hash=script.content_hash,
    )
    return script


def _verify_script(body: AudioRequest) -> None:
    """The audio endpoints re-derive the content hash from the script the
    client sends back: a tampered transcript is a 422, not a render."""
    from app.services.meditation.generate import _content_hash

    expected = _content_hash(
        language=body.script.language,
        theme=body.script.resolved_theme,
        duration_seconds=body.script.duration_seconds,
        voice_speed=body.script.voice_speed,
        context=body.script.context,
        eyes_open=body.script.eyes_open,
        preferences=body.script.preferences,
        seed=body.script.seed,
        transcript=body.script.transcript,
    )
    if expected != body.script.content_hash:
        raise ApiError(
            422,
            "script_hash_mismatch",
            "The script no longer matches its safety check; please generate it again.",
        )


@router.post("/audio")
async def render_audio(
    user: CurrentUser,
    body: AudioRequest,
    service: ServiceDep,
) -> Response:
    """Render the script locally and stream the WAV back.

    Cache state and the content hash travel in headers (never in the body,
    so nothing here needs to carry script text a second time)."""
    del user
    _verify_script(body)
    try:
        result = service.renderer.render(body.script, words_per_minute=WORDS_PER_MINUTE_BASE)
    except TtsRenderError as err:
        log.warning("meditation_tts_unavailable", provider=service.renderer.provider)
        raise ApiError(
            503,
            "meditation_audio_unavailable",
            "Audio is not available on this deployment right now; the written script still works.",
        ) from err
    return Response(
        content=result.wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Content-Sha256": result.content_hash,
            "X-Render-Cache": result.cache,
            "X-Render-Provider": result.provider,
        },
    )


@router.post("/audio/captions", response_model=CaptionsOut)
async def render_captions(
    user: CurrentUser,
    body: AudioRequest,
    service: ServiceDep,
) -> CaptionsOut:
    """The transcript and per-section captions (estimated timing, said so).

    Sharing one render with the audio endpoint means the cache is checked
    the same way; the captions themselves are a pure function of the
    script's sections."""
    del user
    _verify_script(body)
    try:
        result = service.renderer.render(body.script, words_per_minute=WORDS_PER_MINUTE_BASE)
    except TtsRenderError as err:
        log.warning("meditation_tts_unavailable", provider=service.renderer.provider)
        raise ApiError(
            503,
            "meditation_audio_unavailable",
            "Audio is not available on this deployment right now; the written script still works.",
        ) from err
    return CaptionsOut(
        content_hash=result.content_hash,
        transcript=result.transcript,
        duration_seconds=result.duration_seconds,
        provider=result.provider,
        cache=result.cache,
        captions=list(result.captions),
    )


__all__ = [
    "AudioRequest",
    "CaptionsOut",
    "MeditationRequest",
    "MeditationScript",
    "MeditationService",
    "build_meditation_service",
    "router",
]
