"""Chat: sessions, messages, and the streaming reply.

Every route here is a thin skin over :mod:`app.services.chat`. The endpoint's
jobs are the HTTP ones — authenticate, enforce consent, resolve ``(user,
session id)`` to a conversation the caller owns, and shape the response. The
pipeline itself (validation, rate limit, crisis gate, redaction, emotion,
prompt, LLM, output guard, persistence) lives in the orchestrator so there is one
implementation for the JSON and the streaming surface.

==========================================================  =========================
route                                                       what it does
==========================================================  =========================
``POST /chat/sessions``                                     open a session. Ephemeral
                                                            (in memory, 30 min idle)
                                                            unless ``save_history``,
                                                            which needs ``store_chat``
``GET  /chat/sessions/{id}``                                a session's public info
``GET  /chat/sessions/{id}/messages``                       its history (owner only)
``POST /chat/sessions/{id}/messages``                       one message, JSON reply
``POST /chat/sessions/{id}/stream``                         one message, SSE reply
``GET  /chat/sessions/{id}/stream?message=…``               the same, for clients
                                                            that can only issue GETs
==========================================================  =========================

**Ownership.** A session that is not yours is a ``404 session_not_found``,
identical to one that does not exist. No route reveals that another user's
session id is real.

**Consent.** Opening a chat and sending to one need ``ai_disclosure`` + ``terms``
(unchanged since Day 4). *Saving* needs ``store_chat`` on top. Reading your own
history needs only authentication — you do not need to consent again to see data
you already have.

**Streaming (SSE).** ``text/event-stream`` with two kinds of event::

    event: token
    data: {"text": "I hear that "}

    event: final
    data: {"reply": "...", "replaced": false, "session_id": "...",
           "message_id": "...", "metadata": {"risk_level": "...", "emotion": "...",
           "response_type": "...", "resources": [...], ...}}

Problems that can be known before the first byte (bad token, 404, 422, 429,
503) are ordinary HTTP errors, not in-stream events: the pipeline is advanced to
its first event before the response starts. ``event: error`` exists only for a
failure *after* streaming began. ``final.reply`` is authoritative; when
``replaced`` is true the client must swap its rendered text for it.

**GET streaming and privacy.** ``GET .../stream`` takes the message in the query
string because that is the only way a GET can carry one. Query strings are
recorded by proxies and CDNs far more often than bodies, so clients should
prefer ``POST``. Authentication is the ``Authorization`` header either way.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Body, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, require_consent
from app.core.errors import ApiError
from app.models.enums import ConsentKind
from app.models.user import User
from app.services.chat import (
    AdmittedMessage,
    ChatOrchestrator,
    ChatReply,
    ChatSessionService,
    Conversation,
    OpenedSession,
    SessionInfo,
    StreamEvent,
    TokenEvent,
    build_orchestrator,
)

router = APIRouter(prefix="/chat", tags=["chat"])

#: A chat may only start (and continue) once these are granted at their current versions.
ChatConsentGate = require_consent(ConsentKind.AI_DISCLOSURE, ConsentKind.TERMS)

#: Hard ceiling on the request body's message field, in characters. The
#: configurable, user-facing limit (``CHAT_MAX_MESSAGE_CHARS``) is enforced by the
#: orchestrator with a curated error; this only keeps an absurd payload from
#: being parsed at all.
MAX_PAYLOAD_CHARS = 20_000

_log = structlog.get_logger()


# --------------------------------------------------------------------------- #
# Schemas                                                                       #
# --------------------------------------------------------------------------- #


class CreateSessionIn(BaseModel):
    """Optional body for ``POST /chat/sessions``."""

    #: Keep this conversation in the database (encrypted). Needs ``store_chat``.
    #: The default is an ephemeral session that leaves no rows behind.
    save_history: bool = False


class ChatSessionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    ended_at: datetime | None
    #: True for a saved session; False for an in-memory one.
    persistent: bool
    #: When an ephemeral session lapses if nothing more is said; ``null`` if saved.
    expires_at: datetime | None


class MessageIn(BaseModel):
    """One user message. ``region`` / ``locale`` override the profile for this turn."""

    message: str = Field(min_length=1, max_length=MAX_PAYLOAD_CHARS)
    #: ISO region for the helplines (``IN``, ``US``, …). Falls back to the profile.
    region: str | None = Field(default=None, max_length=16)
    #: UI language for pre-written replies (``en``, ``hi``, ``bn``). Falls back to the profile.
    locale: str | None = Field(default=None, max_length=16)


class StoredMessageOut(BaseModel):
    id: uuid.UUID
    role: str
    text: str
    risk_level: int
    emotion: str | None
    created_at: datetime


class MessagesOut(BaseModel):
    session_id: uuid.UUID
    persistent: bool
    messages: list[StoredMessageOut]


# --------------------------------------------------------------------------- #
# Dependencies                                                                  #
# --------------------------------------------------------------------------- #


def get_chat_service(request: Request) -> ChatSessionService:
    """The session service installed by :func:`app.main.create_app`."""
    service: ChatSessionService = request.app.state.chat_sessions
    return service


def get_orchestrator(request: Request) -> ChatOrchestrator:
    """The orchestrator, built on first use.

    Building it loads the ML artifact and constructs the provider chain; none of
    that should slow application start-up or fail it. Tests and scripts may set
    ``app.state.chat_orchestrator`` to a prepared instance before the first
    request.
    """
    existing: ChatOrchestrator | None = request.app.state.chat_orchestrator
    if existing is None:
        existing = build_orchestrator(request.app.state.settings)
        request.app.state.chat_orchestrator = existing
    return existing


ChatServiceDep = Annotated[ChatSessionService, Depends(get_chat_service)]
OrchestratorDep = Annotated[ChatOrchestrator, Depends(get_orchestrator)]
ConsentedUser = Annotated[User, Depends(ChatConsentGate)]


def _session_out(info: SessionInfo) -> ChatSessionOut:
    return ChatSessionOut(
        id=info.id,
        user_id=info.user_id,
        created_at=info.created_at,
        ended_at=info.ended_at,
        persistent=info.persistent,
        expires_at=info.expires_at,
    )


# --------------------------------------------------------------------------- #
# Sessions                                                                      #
# --------------------------------------------------------------------------- #


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    service: ChatServiceDep,
    user: ConsentedUser,
    payload: Annotated[CreateSessionIn | None, Body()] = None,
) -> ChatSessionOut:
    """Open a chat session.

    Needs the AI-disclosure and terms consents. Without a body the session is
    **ephemeral**: held in memory, forgotten after 30 idle minutes, never written
    to the database. ``{"save_history": true}`` additionally needs ``store_chat``.
    """
    wants_history = payload.save_history if payload is not None else False
    return _session_out(await service.create(user.id, persistent=wants_history))


@router.get("/sessions/{session_id}")
async def get_chat_session(
    session_id: uuid.UUID, service: ChatServiceDep, user: CurrentUser
) -> ChatSessionOut:
    """A session's public info. 404 if it is not yours (or does not exist)."""
    opened = await service.open(user.id, session_id)
    return _session_out(opened.info)


@router.get("/sessions/{session_id}/messages")
async def list_chat_messages(
    session_id: uuid.UUID,
    service: ChatServiceDep,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> MessagesOut:
    """The newest ``limit`` messages, oldest first. Owner only; 404 otherwise."""
    opened = await service.open(user.id, session_id)
    turns = await service.messages(opened, limit=limit)
    return MessagesOut(
        session_id=opened.info.id,
        persistent=opened.info.persistent,
        messages=[
            StoredMessageOut(
                id=turn.id,
                role=turn.role,
                text=turn.text,
                risk_level=turn.risk_level,
                emotion=turn.emotion,
                created_at=turn.created_at,
            )
            for turn in turns
        ],
    )


# --------------------------------------------------------------------------- #
# Messages                                                                      #
# --------------------------------------------------------------------------- #


async def _admit(
    orchestrator: ChatOrchestrator,
    service: ChatSessionService,
    user: User,
    session_id: uuid.UUID,
    payload: MessageIn,
) -> tuple[AdmittedMessage, OpenedSession]:
    """Step 1 plus session resolution, shared by the JSON and streaming routes.

    Validation and the rate limit come first, then ownership; every failure here
    is an ordinary HTTP error raised before any response bytes are written.
    """
    admitted = orchestrator.admit(
        user_id=user.id,
        text=payload.message,
        region=payload.region or user.region,
        locale=payload.locale or user.language,
    )
    opened = await service.open(user.id, session_id, for_send=True)
    return admitted, opened


@router.post("/sessions/{session_id}/messages")
async def send_chat_message(
    session_id: uuid.UUID,
    payload: MessageIn,
    service: ChatServiceDep,
    orchestrator: OrchestratorDep,
    user: ConsentedUser,
) -> ChatReply:
    """Send one message and get the whole reply as JSON.

    ``metadata`` carries ``risk_level``, ``emotion``, ``response_type`` and the
    ``resources`` the UI needs to show the CrisisCard. A ``crisis`` response never
    involved the model.
    """
    admitted, opened = await _admit(orchestrator, service, user, session_id, payload)
    return await orchestrator.respond(admitted, opened.conversation)


def _frame(event: str, data: dict[str, Any]) -> bytes:
    """One Server-Sent Event frame. JSON on a single line, so no data is ever split."""
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n".encode()


def _event_frame(event: StreamEvent) -> bytes:
    if isinstance(event, TokenEvent):
        return _frame("token", {"text": event.text})
    reply = event.reply
    return _frame(
        "final",
        {
            "reply": reply.reply,
            "replaced": event.replaced,
            "session_id": str(reply.session_id),
            "message_id": str(reply.message_id) if reply.message_id else None,
            "metadata": reply.metadata.model_dump(mode="json"),
        },
    )


async def _sse_body(first: StreamEvent, rest: AsyncIterator[StreamEvent]) -> AsyncIterator[bytes]:
    """Frame the primed first event, then the rest; close the pipeline on exit."""
    try:
        yield _event_frame(first)
        async for event in rest:
            yield _event_frame(event)
    except ApiError as exc:
        yield _frame("error", {"code": exc.code, "message": exc.message})
    except Exception as exc:
        _log.error("chat_stream_failed", error_type=type(exc).__name__)
        yield _frame(
            "error",
            {"code": "internal_error", "message": "Something went wrong. Please try again."},
        )
    finally:
        aclose = getattr(rest, "aclose", None)
        if aclose is not None:
            await aclose()


async def _stream_response(
    orchestrator: ChatOrchestrator, admitted: AdmittedMessage, conversation: Conversation
) -> StreamingResponse:
    """Advance the pipeline to its first event, then hand the rest to the client.

    Doing the first step *before* returning means a failure in it (no safety
    verdict → 503) is a real HTTP status, not a 200 with an error buried in the
    body.
    """
    events = orchestrator.stream(admitted, conversation)
    try:
        first = await anext(events)
    except StopAsyncIteration as exc:  # pragma: no cover - the pipeline always yields
        raise ApiError(500, "internal_error", "Internal server error") from exc
    return StreamingResponse(
        _sse_body(first, events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/stream")
async def stream_chat_message_post(
    session_id: uuid.UUID,
    payload: MessageIn,
    service: ChatServiceDep,
    orchestrator: OrchestratorDep,
    user: ConsentedUser,
) -> StreamingResponse:
    """Send one message and stream the reply as Server-Sent Events."""
    admitted, opened = await _admit(orchestrator, service, user, session_id, payload)
    return await _stream_response(orchestrator, admitted, opened.conversation)


@router.get("/sessions/{session_id}/stream")
async def stream_chat_message_get(
    session_id: uuid.UUID,
    service: ChatServiceDep,
    orchestrator: OrchestratorDep,
    user: ConsentedUser,
    message: Annotated[str, Query(min_length=1, max_length=MAX_PAYLOAD_CHARS)],
    region: Annotated[str | None, Query(max_length=16)] = None,
    locale: Annotated[str | None, Query(max_length=16)] = None,
) -> StreamingResponse:
    """The same stream for clients that can only issue GET (prefer POST — see module docs)."""
    payload = MessageIn(message=message, region=region, locale=locale)
    admitted, opened = await _admit(orchestrator, service, user, session_id, payload)
    return await _stream_response(orchestrator, admitted, opened.conversation)
