"""Day 44: the agentic care navigator.

A tool-using AI layer with a human in the loop, built
on the repo's safety conventions:

* it sits **behind** the chat safety pipeline
  (:mod:`app.services.agent.safety_gate`) — at risk
  >= MEDIUM it does not run, and the deterministic
  crisis flow answers instead;
* its toolset is an allow-list of five tools
  (:mod:`app.services.agent.tools`) — no web browsing,
  no payments, no bookings, no sending on the user's
  behalf;
* every effecting tool pauses for an explicit
  confirmation card (:mod:`app.services.agent.confirmation`);
* every tool call, executed or rejected, is in the
  transparent action log (:mod:`app.services.agent.action_log`);
* tool output is data, sanitised and scanned for
  instruction-shaped text before the model sees it
  (:mod:`app.services.agent.sanitise`);
* the loop is bounded by steps and a token budget
  (:mod:`app.services.agent.loop`).

The curated directory it searches lives in
``app/content/resources/directory.json`` and is also
exposed read-only as an MCP server (``mcp-server/``).
"""

from app.services.agent.action_log import ActionLog
from app.services.agent.directory import (
    DirectoryEntry,
    DirectoryStore,
    load_directory,
)
from app.services.agent.llm import AgentLLM, FakeAgentLLM
from app.services.agent.loop import AgentLoop
from app.services.agent.safety_gate import AgentSafetyGate, GateOutcome
from app.services.agent.service import AgentNavigator
from app.services.agent.session import AgentSessionService
from app.services.agent.tools import TOOL_REGISTRY, ToolSpec
from app.services.agent.types import (
    AgentLLMResponse,
    AgentMessage,
    AgentTurnResult,
    ConfirmationRequest,
    PendingAction,
    ToolCallRequest,
    TurnState,
)

__all__ = [
    "TOOL_REGISTRY",
    "ActionLog",
    "AgentLLM",
    "AgentLLMResponse",
    "AgentLoop",
    "AgentMessage",
    "AgentNavigator",
    "AgentSafetyGate",
    "AgentSessionService",
    "AgentTurnResult",
    "ConfirmationRequest",
    "DirectoryEntry",
    "DirectoryStore",
    "FakeAgentLLM",
    "GateOutcome",
    "PendingAction",
    "ToolCallRequest",
    "ToolSpec",
    "TurnState",
    "load_directory",
]
