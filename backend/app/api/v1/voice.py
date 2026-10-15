"""Voice endpoints and the authenticated real-time PCM WebSocket.

The WebSocket accepts short base64-wrapped mono PCM16 chunks.  Base64 keeps the
protocol inspectable in tests and avoids browser-specific binary framing; audio
is still streamed chunk-by-chunk, kept in RAM only, and discarded when the
utterance/session ends.  Words from STT always pass through the existing chat
orchestrator — there is no voice-specific LLM path.

Browser WebSockets cannot attach an ``Authorization`` header.  The access token
is therefore sent in a second ``Sec-WebSocket-Protocol`` value
(``bearer.<JWT>``), never in the URL/query string.  The server selects only the
non-secret ``manovia.v1`` protocol and never reflects/logs the credential.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.content import load_consent_documents
from app.core.config import Settings, get_settings
from app.core.tokens import ACCESS_TOKEN_TYPE, TokenError, TokenExpiredError
from app.db.repos import ConsentRepository, RefreshTokenRepository, UserRepository
from app.models.enums import ConsentKind
from app.models.user import User
from app.services.chat import ChatOrchestrator, build_orchestrator
from app.services.voice.realtime import (
    SILENCE_TIMEOUT_MESSAGE,
    RealtimeVoiceSession,
    VoiceProtocolError,
    VoiceRuntime,
    decode_pcm_chunk,
)

router = APIRouter(tags=["voice"])

VOICE_SUBPROTOCOL = "manovia.v1"


class TranscriptionResponse(BaseModel):
    text: str
    language: str
    language_probability: float


@router.post("/voice/transcribe", response_model=TranscriptionResponse)
async def transcribe(
    user: CurrentUser,
    audio: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> TranscriptionResponse:
    """Day 39 compatibility endpoint; live audio uses the documented WebSocket runtime."""
    del settings, user
    from app.services.voice.stt import transcribe_audio

    return TranscriptionResponse(**transcribe_audio(audio))


@router.get("/voice/voices")
async def get_voices(user: CurrentUser) -> dict[str, object]:
    """List reviewed local language choices without exposing model paths."""
    del user
    return {
        "voices": [
            {"id": "en_US", "language": "en"},
            {"id": "hi_IN", "language": "hi"},
            {"id": "bn_IN", "language": "bn"},
        ]
    }


@router.post("/voice/synthesize")
async def synthesize(
    user: CurrentUser,
    text: Annotated[str, Form(...)],
    language: Annotated[str, Form()] = "en",
    speed: Annotated[float, Form()] = 1.0,
) -> Response:
    """Legacy local TTS endpoint; sentence streaming uses the WebSocket runtime."""
    del user
    from app.services.voice.stt import generate_speech

    audio_bytes = generate_speech(text, language, speed)
    return Response(content=audio_bytes, media_type="audio/wav")


def _websocket_bearer(websocket: WebSocket) -> str | None:
    """Read the token from a subprotocol header without ever putting it in a URL."""
    offered = websocket.headers.get("sec-websocket-protocol", "")
    values = [value.strip() for value in offered.split(",") if value.strip()]
    if VOICE_SUBPROTOCOL not in values:
        return None
    for value in values:
        if value.startswith("bearer."):
            token = value.removeprefix("bearer.").strip()
            return token or None
    return None


async def _websocket_user(websocket: WebSocket) -> User | None:
    """Authenticate and consent-gate a socket exactly like chat HTTP routes."""
    raw = _websocket_bearer(websocket)
    if raw is None:
        return None
    tokens = websocket.app.state.token_service
    try:
        claims = tokens.verify(raw, expected_type=ACCESS_TOKEN_TYPE)
    except (TokenError, TokenExpiredError):
        return None

    database = websocket.app.state.db
    async with database.session_factory() as session:
        user = await UserRepository(session).get(claims.subject)
        if user is None or user.is_deleted:
            return None
        if claims.family_id is not None and not await RefreshTokenRepository(
            session
        ).family_is_active(claims.family_id):
            return None
        documents = load_consent_documents()
        grants = (
            ConsentKind.AI_DISCLOSURE,
            ConsentKind.TERMS,
        )
        repo = ConsentRepository(session)
        for kind in grants:
            if not await repo.is_granted(
                user_id=user.id, kind=kind, version=documents.version_for(kind)
            ):
                return None
        return user


def _start_fields(payload: dict[str, object], user: User) -> tuple[str, str | None]:
    locale = payload.get("locale", user.language)
    region = payload.get("region", user.region)
    if not isinstance(locale, str) or len(locale) > 16:
        raise VoiceProtocolError("start locale is invalid")
    if region is not None and (not isinstance(region, str) or len(region) > 16):
        raise VoiceProtocolError("start region is invalid")
    return locale, region


@router.websocket("/voice/sessions/{session_id}/stream")
async def realtime_voice_stream(websocket: WebSocket, session_id: str) -> None:
    """Run one authenticated streaming voice conversation.

    The client first sends ``{"type":"start", ...}``, then many
    ``{"type":"audio","pcm":"..."}`` chunks.  A voiced chunk while
    assistant audio is active is a barge-in: the server cancels further sentence
    TTS and sends ``barge_in`` immediately; the browser stops already scheduled
    local audio synchronously.
    """
    if not websocket.app.state.settings.voice_realtime_enabled:
        await websocket.close(code=4403)
        return
    try:
        parsed_session_id = uuid.UUID(session_id)
    except ValueError:
        await websocket.close(code=4400)
        return
    user = await _websocket_user(websocket)
    if user is None:
        await websocket.close(code=4401)
        return

    try:
        service = websocket.app.state.chat_sessions
        opened = await service.open(user.id, parsed_session_id, for_send=True)
    except Exception:
        # Do not distinguish a missing/other-owner/ended session over a socket.
        await websocket.close(code=4404)
        return

    await websocket.accept(subprotocol=VOICE_SUBPROTOCOL)
    runtime: VoiceRuntime = websocket.app.state.voice_runtime
    orchestrator: ChatOrchestrator | None = websocket.app.state.chat_orchestrator
    if orchestrator is None:
        orchestrator = build_orchestrator(websocket.app.state.settings)
        websocket.app.state.chat_orchestrator = orchestrator
    voice: RealtimeVoiceSession | None = None
    started = False

    async def send(event: dict[str, object]) -> None:
        await websocket.send_json(event)

    try:
        await send(
            {
                "type": "ready",
                "sample_rate_hz": runtime.settings.voice_sample_rate_hz,
                "silence_timeout_seconds": runtime.settings.voice_silence_timeout_seconds,
                "barge_in_target_ms": runtime.settings.voice_barge_in_target_ms,
            }
        )
        while True:
            try:
                payload = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=runtime.settings.voice_silence_timeout_seconds if started else 120,
                )
            except TimeoutError:
                if voice is not None:
                    await send(
                        {
                            "type": "timeout",
                            "message": SILENCE_TIMEOUT_MESSAGE,
                        }
                    )
                    await voice.close()
                await websocket.close(code=1000)
                return
            if not isinstance(payload, dict):
                await send({"type": "error", "code": "invalid_message"})
                continue
            kind = payload.get("type")
            if kind == "start":
                if started:
                    await send({"type": "error", "code": "already_started"})
                    continue
                locale, region = _start_fields(payload, user)
                # Pre-synthesise deterministic reviewed crisis messages before
                # microphone chunks arrive. The cache is process-memory only.
                await runtime.prewarm_crisis_audio(locale=locale, region=region)
                voice = RealtimeVoiceSession(
                    settings=runtime.settings,
                    orchestrator=orchestrator,
                    conversation=opened.conversation,
                    locale=locale,
                    region=region,
                    send=send,
                    transcriber_factory=runtime.transcriber_factory,
                    synthesizer=runtime.synthesizer,
                    crisis_audio=runtime.crisis_audio,
                )
                started = True
                await send({"type": "started", "storage": "memory_only"})
                continue
            if not started or voice is None:
                await send({"type": "error", "code": "start_required"})
                continue
            if kind == "audio":
                try:
                    pcm = decode_pcm_chunk(
                        payload.get("pcm"), max_bytes=runtime.settings.voice_max_chunk_bytes
                    )
                    await voice.on_audio(pcm)
                except VoiceProtocolError:
                    await send({"type": "error", "code": "invalid_audio"})
                continue
            if kind == "finish":
                await voice.finish_now()
                continue
            if kind == "continue":
                await send({"type": "continue_ready"})
                continue
            if kind == "ping":
                await send({"type": "pong"})
                continue
            if kind == "end":
                await voice.close()
                await send({"type": "ended"})
                await websocket.close(code=1000)
                return
            await send({"type": "error", "code": "unknown_message"})
    except WebSocketDisconnect:
        return
    finally:
        if voice is not None:
            await voice.close()
