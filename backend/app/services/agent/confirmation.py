"""Human-in-the-loop: the confirmation gate.

Every tool with an external effect pauses the loop and
hands the person a **confirmation card** — the tool's name,
a plain-language summary of exactly what would happen, and
the validated arguments. The tool executes only after an
explicit ``allow``; a ``deny`` (or silence past expiry) means
it never runs. Read-only tools never ask.

The gate is not a modal the model controls: the *loop*
stops, returns control to the caller, and only resumes when
the caller presents the person's decision. A model cannot
confirm its own action.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta

from app.services.agent.types import (
    ConfirmationRequest,
    PendingAction,
    ToolCallRequest,
    TurnState,
)

#: How long a confirmation card stays valid.
CONFIRMATION_TTL: timedelta = timedelta(minutes=15)

#: Placards for the one effecting tool in the allow-list.
#: A new effecting tool needs a card here — that is the
#: point of the gate: nothing external happens by default.
CARD_TITLES: dict[str, str] = {
    "set_reminder": "Set a reminder on this device?",
}


def summarise(tool: str, arguments: dict[str, object]) -> str:
    """A plain-language one-liner for the card."""
    if tool == "set_reminder":
        text = arguments.get("text", "")
        when = arguments.get("when", "")
        return f'Reminder "{text}" at {when}.'
    return f"Run {tool} with {arguments}."


def build_confirmation(request: ToolCallRequest, state: TurnState) -> PendingAction:
    """Pause the turn and build the card the person will see."""
    now = datetime.now().astimezone()
    confirmation = ConfirmationRequest(
        pending_id=uuid.uuid4().hex,
        tool=request.name,
        title=CARD_TITLES.get(request.name, f"Allow {request.name}?"),
        summary=summarise(request.name, request.arguments),
        detail=dict(request.arguments),
        created_at=now,
        expires_at=now + CONFIRMATION_TTL,
    )
    return PendingAction(
        pending_id=confirmation.pending_id,
        request=request,
        confirmation=confirmation,
        turn_snapshot=state,
    )


def new_pending_id() -> str:
    """An unguessable id for a pending action."""
    return secrets.token_hex(16)
