"""The agentic navigator's HTTP surface.

Two route groups, both behind the same auth and consent
gate as chat, and both **behind the chat safety
pipeline**:

* ``POST /agent/sessions`` — open a navigator session.
* ``POST /agent/sessions/{id}/chat`` — one message.
  The safety gate runs *first*; at risk >= MEDIUM the
  deterministic crisis flow answers and the agent is
  never invoked (see :mod:`app.services.agent.service`).
* ``POST /agent/sessions/{id}/confirmations/{pending}`` —
  the person's answer to a confirmation card.

Ownership follows the chat routes' rule: a session id
that is not yours is a 404 identical to a nonexistent
one, so ids leak nothing.

Everything the agent did comes back in ``action_log`` —
proposed, executed, rejected, confirmed, denied,
quarantined — because transparency is the point.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from app.api.deps import require_consent
from app.core.errors import ApiError
from app.models.enums import ConsentKind
from app.models.user import User
from app.services.agent.service import (
    AgentNavigator,
    UnknownAgentSession,
)
from app.services.agent.types import AgentTurnResult

router = APIRouter(prefix="/agent", tags=["agent"])

#: Opening a session and chatting need the same consent
#: as chat; the navigator writes nothing persistent.
NavigatorConsentGate = require_consent(ConsentKind.AI_DISCLOSURE, ConsentKind.TERMS)

#: One navigator message, bounded like a chat message.
MAX_AGENT_MESSAGE_CHARS = 4_000


class AgentChatIn(BaseModel):
    """One navigator message."""

    message: str = Field(min_length=1, max_length=MAX_AGENT_MESSAGE_CHARS)
    region: str | None = Field(default=None, max_length=16)
    locale: str | None = Field(default=None, max_length=16)


class ConfirmationIn(BaseModel):
    """The person's answer to a confirmation card."""

    decision: Literal["allow", "deny"]


class AgentSessionOut(BaseModel):
    session_id: uuid.UUID


class AgentToolsOut(BaseModel):
    #: The allow-list, so a UI can render exactly what
    #: the navigator can and cannot do.
    tools: dict[str, Any]


def get_navigator(request: Request) -> AgentNavigator:
    """The navigator installed by :func:`app.main.create_app`."""
    navigator: AgentNavigator | None = request.app.state.agent_navigator
    if navigator is None:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="agent_not_enabled",
            message="The care navigator is not enabled on this deployment.",
        )
    return navigator


NavigatorDep = Annotated[AgentNavigator, Depends(get_navigator)]
ConsentedUser = Annotated[User, Depends(NavigatorConsentGate)]


@router.get("/tools")
async def list_agent_tools(navigator: NavigatorDep) -> AgentToolsOut:
    """The allow-listed toolset, for transparency."""
    return AgentToolsOut(tools=navigator.tools)


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_agent_session(user: ConsentedUser, navigator: NavigatorDep) -> AgentSessionOut:
    """Open a navigator session (ephemeral, in memory)."""
    state = navigator.open_session(str(user.id))
    return AgentSessionOut(session_id=uuid.UUID(state.session_id))


@router.post("/sessions/{session_id}/chat")
async def navigator_chat(
    session_id: uuid.UUID,
    payload: AgentChatIn,
    navigator: NavigatorDep,
    user: ConsentedUser,
) -> AgentTurnResult:
    """One navigator turn: safety gate first, then the agent."""
    try:
        result = await navigator.chat(
            user_id=str(user.id),
            session_id=str(session_id),
            message=payload.message,
            region=payload.region,
            locale=payload.locale,
        )
    except UnknownAgentSession:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="agent_session_not_found",
            message="That navigator session was not found.",
        ) from None
    return result


@router.post("/sessions/{session_id}/confirmations/{pending_id}")
async def answer_confirmation(
    session_id: uuid.UUID,
    pending_id: str,
    payload: ConfirmationIn,
    navigator: NavigatorDep,
    user: ConsentedUser,
) -> AgentTurnResult:
    """Allow or deny a paused, effecting tool call."""
    try:
        result = await navigator.confirm(
            user_id=str(user.id),
            session_id=str(session_id),
            pending_id=pending_id,
            decision=payload.decision,
        )
    except UnknownAgentSession:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="agent_session_not_found",
            message="That navigator session was not found.",
        ) from None
    return result
