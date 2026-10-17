"""The shapes the agentic navigator passes around.

Nothing in this module holds a person's words except the final reply and
the user's own tool arguments (which are bounded strings validated
against strict schemas). The action log never carries raw message text —
AGENTS.md safety rule 5 — only validated, bounded tool arguments and
neutral outcome summaries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.llm.base import LLMUsage
from app.services.safety.base import RiskLevel

#: What a turn produced. ``crisis`` means the deterministic crisis flow
#: answered and the agent never ran. ``confirmation_required`` means the
#: loop paused for a human decision. ``refusal`` means the request was
#: outside the allow-listed toolset and nothing executed.
TurnKind = Literal["agent", "crisis", "refusal", "confirmation_required", "degraded"]

#: Every event the transparent action log can carry. Closed vocabulary:
#: the UI renders cards from these, so a new event is a schema change.
LogEvent = Literal[
    "tool_proposed",
    "tool_executed",
    "tool_rejected",
    "confirmation_required",
    "confirmation_allowed",
    "confirmation_denied",
    "injection_quarantined",
    "step_limit_reached",
    "token_budget_exhausted",
    "safety_gate_blocked",
    "final_answer",
]


class ToolCallRequest(BaseModel):
    """One tool call the model asked for, before any validation."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentMessage(BaseModel):
    """One message in the loop's transcript.

    ``role`` is deliberately wider than the chat LLM's: the agent speaks
    to a model with tools, so the transcript holds tool results too. A
    ``tool`` message is *data* — it is framed as such in the prompt and
    may never be obeyed (see :mod:`app.services.agent.sanitise`).
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCallRequest] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class AgentLLMResponse(BaseModel):
    """What the model returned: prose, tool calls, or both."""

    text: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    usage: LLMUsage | None = None
    finish_reason: str = "stop"


class ConfirmationRequest(BaseModel):
    """The card a person must answer before an effecting tool runs."""

    pending_id: str = Field(min_length=8, max_length=64)
    tool: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=400)
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime


class ConfirmationResolution(BaseModel):
    """The person's answer to a confirmation card."""

    decision: Literal["allow", "deny"]
    decided_at: datetime


class ActionLogEntry(BaseModel):
    """One transparent record of something the agent did or tried to do.

    The whole log is shown to the user after every turn — transparency is
    the feature, not the debugging aid. Carries no raw message text.
    """

    seq: int
    at: datetime
    event: LogEvent
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    outcome: str | None = None
    reason: str | None = None


class PendingAction(BaseModel):
    """A tool call paused at the confirmation gate."""

    pending_id: str
    request: ToolCallRequest
    confirmation: ConfirmationRequest
    turn_snapshot: TurnState

    class Config:
        arbitrary_types_allowed = True


class TurnState(BaseModel):
    """Everything a turn remembers between confirmation pauses."""

    session_id: str = Field(min_length=1, max_length=120)
    user_id: str = Field(min_length=1, max_length=120)
    #: The person's region hint (``IN``, ``IN-WB``, …) and UI language,
    #: used to frame the loop's context line — never sent anywhere else.
    region: str | None = None
    locale: str | None = None
    transcript: list[AgentMessage] = Field(default_factory=list)
    log: list[ActionLogEntry] = Field(default_factory=list)
    steps_used: int = 0
    tokens_used: int = 0
    pending: list[PendingAction] = Field(default_factory=list)


class AgentTurnResult(BaseModel):
    """One answered turn, as the API serves it."""

    reply: str
    response_type: TurnKind
    risk_level: str = RiskLevel.NONE.label
    action_log: list[ActionLogEntry] = Field(default_factory=list)
    confirmation: ConfirmationRequest | None = None
    #: Helplines for the person's region, only on a crisis turn.
    crisis_resources: list[dict[str, Any]] = Field(default_factory=list)
    steps_used: int = 0
    tokens_used: int = 0
    tools_executed: list[str] = Field(default_factory=list)


class GateDecision(BaseModel):
    """The safety gate's verdict for one inbound message."""

    allowed: bool
    risk_level: RiskLevel
    crisis_reply: str | None = None
    crisis_resources: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return not self.allowed


# PendingAction turns a snapshot of TurnState, which is defined above it;
# pydantic resolves the forward reference lazily.
PendingAction.model_rebuild()
