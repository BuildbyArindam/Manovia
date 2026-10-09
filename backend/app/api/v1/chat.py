"""Chat surface (partial): opening a session, gated on consent.

Message send/receive, the crisis gate and the output-safety check arrive with
the chat milestone. This module exists now because the consent gate
(:func:`app.api.deps.require_consent`) needs a real consumer: starting a chat
is exactly what must not happen before the AI disclosure and terms are
granted, and the integration tests drive this endpoint end to end.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.api.deps import SessionDep, require_consent
from app.db.repos import ChatRepository
from app.models.enums import ConsentKind
from app.models.user import User

router = APIRouter(prefix="/chat", tags=["chat"])

#: A chat may only start once these are granted at their current versions.
ChatConsentGate = require_consent(ConsentKind.AI_DISCLOSURE, ConsentKind.TERMS)


class ChatSessionOut(BaseModel):
    id: UUID
    user_id: UUID
    created_at: datetime
    ended_at: datetime | None


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    session: SessionDep,
    user: Annotated[User, Depends(ChatConsentGate)],
) -> ChatSessionOut:
    """Open a chat session. Requires the AI disclosure and terms consents."""
    chat_session = await ChatRepository(session).create(user_id=user.id)
    await session.commit()
    return ChatSessionOut(
        id=chat_session.id,
        user_id=chat_session.user_id,
        created_at=chat_session.created_at,
        ended_at=chat_session.ended_at,
    )
