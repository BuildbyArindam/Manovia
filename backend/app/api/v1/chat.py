"""Chat surface — Day 11 orchestrator, persistent + ephemeral sessions.

Endpoints:

* POST   /chat/sessions
* GET    /chat/sessions
* GET    /chat/sessions/{id}
* GET    /chat/sessions/{id}/messages
* POST   /chat/sessions/{id}/messages          (JSON)
* GET    /chat/sessions/{id}/messages/stream   (SSE)
* POST   /chat/sessions/{id}/messages/stream   (SSE)

Ephemeral sessions live only in memory for 30 min when the user has not granted
``store_chat`` consent or explicitly asks for an ephemeral session. No DB rows
are written for them, which is verified by the Day 11 suite.

All message send paths run through :class:`~app.services.chat.orchestrator.ChatOrchestrator`
which implements the 9-step pipeline: validate → safety (rules+ML) → redaction
→ emotion → retrieval stub → prompt build → LLM → output guard stub → persist.

If safety is HIGH/IMMINENT the LLM is never called (call count 0), the
deterministic crisis copy is returned, and response_type is "crisis".
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import (
    ConsentDocumentsDep,
    CurrentUser,
    EphemeralStoreDep,
    OrchestratorDep,
    SessionDep,
    require_consent,
)
from app.core.errors import ApiError
from app.db.repos import ChatRepository, ConsentRepository
from app.models.enums import ConsentKind
from app.models.user import User
from app.services.chat.ephemeral import EphemeralSession

router = APIRouter(prefix="/chat", tags=["chat"])

ChatConsentGate = require_consent(ConsentKind.AI_DISCLOSURE, ConsentKind.TERMS)
ChatStoreConsentGate = require_consent(
    ConsentKind.AI_DISCLOSURE, ConsentKind.TERMS, ConsentKind.STORE_CHAT
)


# --- Request / Response models ----------------------------------------------


class CreateSessionIn(BaseModel):
    store: bool = Field(
        default=False,
        description="True to persist history (requires store_chat consent); False for ephemeral.",
    )
    region: str | None = Field(default=None, max_length=16)
    locale: str | None = Field(default=None, max_length=16)


class ChatSessionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    ended_at: datetime | None = None
    ephemeral: bool = False
    store: bool = False


class ChatSessionListOut(BaseModel):
    sessions: list[ChatSessionOut]


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    region: str | None = Field(default=None, max_length=16)
    locale: str | None = Field(default=None, max_length=16)


class ResourceOut(BaseModel):
    id: str
    name: str
    number: str | None = None
    type: str | None = None
    url: str | None = None


class CrisisMessageOut(BaseModel):
    title: str | None = None
    body: str | None = None
    emergency_instruction: str | None = None
    trusted_person: str | None = None
    safety_steps: list[str] | None = None
    helpline_intro: str | None = None
    closing: str | None = None
    disclaimer: str | None = None


class ChatMessageResponse(BaseModel):
    session_id: uuid.UUID
    ephemeral: bool
    user_message: dict[str, Any]
    assistant_message: dict[str, Any]
    risk_level: str
    stored_risk_level: str | None = None
    emotion: str | None = None
    response_type: str
    resources: list[dict[str, Any]] = Field(default_factory=list)
    emergency: dict[str, Any] | None = None
    crisis: dict[str, Any] | None = None
    degraded: bool = False
    provider: str | None = None


class MessageListOut(BaseModel):
    session_id: uuid.UUID
    ephemeral: bool
    messages: list[dict[str, Any]]


# --- Helpers -----------------------------------------------------------------


async def _resolve_session(
    session_id: uuid.UUID,
    user: User,
    db_session: SessionDep,
    ephemeral_store: EphemeralStoreDep,
) -> tuple[Any, bool]:
    """Return (session_obj, is_ephemeral) or raise 404/403.

    Checks ephemeral first, then DB. Ownership is enforced.
    """
    # Ephemeral first
    eph = ephemeral_store.get(session_id)
    if eph is not None:
        if eph.user_id != user.id:
            raise ApiError(403, "forbidden", "You do not own this session.")
        return eph, True

    # Persistent
    repo = ChatRepository(db_session)
    db_sess = await repo.get(session_id)
    if db_sess is not None:
        if db_sess.user_id != user.id:
            raise ApiError(403, "forbidden", "You do not own this session.")
        return db_sess, False

    raise ApiError(404, "not_found", "Chat session not found.")


def _ephemeral_to_out(s: EphemeralSession) -> ChatSessionOut:
    return ChatSessionOut(
        id=s.id,
        user_id=s.user_id,
        created_at=s.created_at,
        ended_at=s.ended_at,
        ephemeral=True,
        store=False,
    )


def _db_to_out(s: Any, ephemeral: bool = False) -> ChatSessionOut:
    return ChatSessionOut(
        id=s.id,
        user_id=s.user_id,
        created_at=s.created_at,
        ended_at=s.ended_at,
        ephemeral=ephemeral,
        store=not ephemeral,
    )


def _message_to_dict(m: Any, content: str) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "role": m.role.value if hasattr(m.role, "value") else str(m.role),
        "content": content,
        "created_at": m.created_at.isoformat()
        if hasattr(m.created_at, "isoformat")
        else str(m.created_at),
        "risk_level": getattr(m, "risk_level", 0),
        "emotion": getattr(m, "emotion", None),
    }


# --- Session endpoints -------------------------------------------------------


@router.post("/sessions", status_code=201)
async def create_chat_session(
    request: Request,
    session: SessionDep,
    ephemeral_store: EphemeralStoreDep,
    documents: ConsentDocumentsDep,
    user: CurrentUser,
    payload: CreateSessionIn | None = None,
) -> ChatSessionOut:
    """Open a chat session.

    * ``store=False`` (default): ephemeral, in-memory TTL 30 min, no DB rows.
      Requires AI disclosure + terms.
    * ``store=True``: persistent, encrypted in DB. Requires store_chat consent
      in addition to the base consents.
    """
    data = payload or CreateSessionIn()
    # Check base consents first (AI disclosure + terms)
    # We reuse the consent repo logic directly so we can branch on store flag
    repo = ConsentRepository(session)
    base_missing = []
    for kind in (ConsentKind.AI_DISCLOSURE, ConsentKind.TERMS):
        version = documents.version_for(kind)
        granted = await repo.is_granted(user_id=user.id, kind=kind, version=version)
        if not granted:
            base_missing.append(kind.value)
    if base_missing:
        raise ApiError(
            403,
            "consent_required",
            f"Consent required: {', '.join(base_missing)}.",
        )

    if data.store:
        # Need store_chat consent as well
        version = documents.version_for(ConsentKind.STORE_CHAT)
        granted = await repo.is_granted(
            user_id=user.id, kind=ConsentKind.STORE_CHAT, version=version
        )
        if not granted:
            raise ApiError(
                403,
                "consent_required",
                "Consent required: store_chat.",
            )
        # Persistent
        chat_session = await ChatRepository(session).create(user_id=user.id)
        await session.commit()
        return ChatSessionOut(
            id=chat_session.id,
            user_id=chat_session.user_id,
            created_at=chat_session.created_at,
            ended_at=chat_session.ended_at,
            ephemeral=False,
            store=True,
        )
    else:
        # Ephemeral
        eph = ephemeral_store.create(user_id=user.id)
        return ChatSessionOut(
            id=eph.id,
            user_id=eph.user_id,
            created_at=eph.created_at,
            ended_at=eph.ended_at,
            ephemeral=True,
            store=False,
        )


@router.get("/sessions")
async def list_chat_sessions(
    db_session: SessionDep,
    ephemeral_store: EphemeralStoreDep,
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ChatSessionListOut:
    """List sessions for the current user — both persistent and ephemeral."""
    repo = ChatRepository(db_session)
    db_sessions = await repo.list_for_user(user.id, limit=limit, offset=offset)
    eph_sessions = ephemeral_store.list_for_user(user.id, limit=limit, offset=offset)

    # Merge by created_at descending, then slice
    all_sessions: list[ChatSessionOut] = []
    for db_s in db_sessions:
        all_sessions.append(_db_to_out(db_s, ephemeral=False))
    for eph_s in eph_sessions:
        all_sessions.append(_ephemeral_to_out(eph_s))

    all_sessions.sort(key=lambda x: x.created_at, reverse=True)
    # Re-apply limit/offset after merge (simple)
    sliced = all_sessions[offset : offset + limit]
    return ChatSessionListOut(sessions=sliced)


@router.get("/sessions/{session_id}")
async def get_chat_session(
    session_id: uuid.UUID,
    db_session: SessionDep,
    ephemeral_store: EphemeralStoreDep,
    user: CurrentUser,
) -> ChatSessionOut:
    sess, is_eph = await _resolve_session(session_id, user, db_session, ephemeral_store)
    if is_eph:
        return _ephemeral_to_out(sess)
    return _db_to_out(sess, ephemeral=False)


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: uuid.UUID,
    db_session: SessionDep,
    ephemeral_store: EphemeralStoreDep,
    user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> MessageListOut:
    _sess, is_eph = await _resolve_session(session_id, user, db_session, ephemeral_store)
    if is_eph:
        msgs = ephemeral_store.list_messages(session_id, limit=limit, offset=offset)
        if msgs is None:
            raise ApiError(404, "not_found", "Chat session not found.")
        return MessageListOut(
            session_id=session_id,
            ephemeral=True,
            messages=[_message_to_dict(m, m.content) for m in msgs],
        )
    else:
        repo = ChatRepository(db_session)
        rows = await repo.list_messages(session_id, limit=limit, offset=offset)
        decrypted = []
        for r in rows:
            try:
                text = repo.message_text(r)
            except Exception:
                text = "[decryption failed]"
            decrypted.append(_message_to_dict(r, text))
        return MessageListOut(
            session_id=session_id,
            ephemeral=False,
            messages=decrypted,
        )


# --- Message send (JSON) -----------------------------------------------------


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: uuid.UUID,
    payload: MessageIn,
    db_session: SessionDep,
    ephemeral_store: EphemeralStoreDep,
    orchestrator: OrchestratorDep,
    user: CurrentUser,
) -> ChatMessageResponse:
    """Send a message and get a JSON reply.

    Runs the full orchestrator pipeline. Returns metadata so the UI can show
    CrisisCard, emotion, etc.
    """
    _sess, is_eph = await _resolve_session(session_id, user, db_session, ephemeral_store)

    # For persistent sessions, we pass db_session; for ephemeral, None
    db_for_orch = None if is_eph else db_session

    result = await orchestrator.handle_message(
        db_session=db_for_orch,
        user=user,
        session_id=session_id,
        content=payload.content,
        is_ephemeral=is_eph,
        region=payload.region,
        locale=payload.locale,
    )

    # Build response
    user_msg = result.user_message
    asst_msg = result.assistant_message

    return ChatMessageResponse(
        session_id=session_id,
        ephemeral=is_eph,
        user_message=_message_to_dict(user_msg, user_msg.content) if user_msg else {},
        assistant_message=_message_to_dict(asst_msg, asst_msg.content)
        if asst_msg
        else {"content": result.reply_text},
        risk_level=result.risk_level,
        stored_risk_level=result.stored_risk_level,
        emotion=result.emotion,
        response_type=result.response_type,
        resources=[
            r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in result.resources
        ],
        emergency=result.emergency.model_dump()
        if result.emergency and hasattr(result.emergency, "model_dump")
        else (dict(result.emergency) if result.emergency else None),
        crisis=result.crisis_message.model_dump()
        if result.crisis_message and hasattr(result.crisis_message, "model_dump")
        else None,
        degraded=result.degraded,
        provider=result.provider,
    )


# --- Streaming ---------------------------------------------------------------


def _sse_event(data: dict[str, Any], event: str | None = None) -> str:
    """Format one SSE event."""
    lines = []
    if event:
        lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


@router.get("/sessions/{session_id}/messages/stream")
async def stream_messages_get(  # type: ignore[no-untyped-def]
    session_id: uuid.UUID,
    db_session: SessionDep,
    ephemeral_store: EphemeralStoreDep,
    orchestrator: OrchestratorDep,
    user: CurrentUser,
    content: str = Query(..., min_length=1, max_length=10000),
    region: str | None = Query(default=None, max_length=16),
    locale: str | None = Query(default=None, max_length=16),
):
    """Stream a reply via SSE — GET variant with query param.

    The query-param form is convenient for EventSource which cannot POST.
    """
    _sess, is_eph = await _resolve_session(session_id, user, db_session, ephemeral_store)
    db_for_orch = None if is_eph else db_session

    async def event_generator():  # type: ignore[no-untyped-def]
        async for chunk in orchestrator.stream_message(
            db_session=db_for_orch,
            user=user,
            session_id=session_id,
            content=content,
            is_ephemeral=is_eph,
            region=region,
            locale=locale,
        ):
            if "token" in chunk:
                yield _sse_event({"token": chunk["token"]}, event="token")
            elif chunk.get("type") == "final":
                yield _sse_event(
                    {
                        "risk_level": chunk.get("risk_level"),
                        "emotion": chunk.get("emotion"),
                        "response_type": chunk.get("response_type"),
                        "resources": chunk.get("resources", []),
                        "degraded": chunk.get("degraded", False),
                        "provider": chunk.get("provider"),
                    },
                    event="done",
                )
                # Also send a final data event with metadata for clients that
                # only listen to data
                yield _sse_event(
                    {
                        "type": "final",
                        "risk_level": chunk.get("risk_level"),
                        "emotion": chunk.get("emotion"),
                        "response_type": chunk.get("response_type"),
                        "resources": chunk.get("resources", []),
                        "degraded": chunk.get("degraded", False),
                    }
                )

    return StreamingResponse(event_generator(), media_type="text/event-stream")  # type: ignore[no-untyped-call]


@router.post("/sessions/{session_id}/messages/stream")
async def stream_messages_post(
    session_id: uuid.UUID,
    payload: MessageIn,
    db_session: SessionDep,
    ephemeral_store: EphemeralStoreDep,
    orchestrator: OrchestratorDep,
    user: CurrentUser,
) -> StreamingResponse:
    """Stream a reply via SSE — POST variant with JSON body."""
    _sess, is_eph = await _resolve_session(session_id, user, db_session, ephemeral_store)
    db_for_orch = None if is_eph else db_session

    async def event_generator():  # type: ignore[no-untyped-def]
        async for chunk in orchestrator.stream_message(
            db_session=db_for_orch,
            user=user,
            session_id=session_id,
            content=payload.content,
            is_ephemeral=is_eph,
            region=payload.region,
            locale=payload.locale,
        ):
            if "token" in chunk:
                yield _sse_event({"token": chunk["token"]}, event="token")
            elif chunk.get("type") == "final":
                yield _sse_event(
                    {
                        "risk_level": chunk.get("risk_level"),
                        "emotion": chunk.get("emotion"),
                        "response_type": chunk.get("response_type"),
                        "resources": chunk.get("resources", []),
                        "degraded": chunk.get("degraded", False),
                        "provider": chunk.get("provider"),
                    },
                    event="done",
                )
                yield _sse_event(
                    {
                        "type": "final",
                        "risk_level": chunk.get("risk_level"),
                        "emotion": chunk.get("emotion"),
                        "response_type": chunk.get("response_type"),
                        "resources": chunk.get("resources", []),
                        "degraded": chunk.get("degraded", False),
                    }
                )

    return StreamingResponse(event_generator(), media_type="text/event-stream")  # type: ignore[no-untyped-call]
