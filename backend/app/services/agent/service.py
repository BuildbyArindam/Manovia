"""The navigator service: what the API routes call.

Two operations, both thin:

* ``chat`` — run the safety gate first (the agent sits
  *behind* the chat safety pipeline), then one turn of
  the agent loop.
* ``confirm`` — answer a pending confirmation card and
  resume the paused turn.

The gate is the whole safety argument in one place:
a person at risk >= MEDIUM gets the deterministic
crisis reply and helplines, and the agent — its model,
its tools, its budget — is never invoked.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.core.config import Settings
from app.services.agent.action_log import ActionLog
from app.services.agent.directory import DirectoryStore, load_directory
from app.services.agent.llm import AgentLLM, FakeAgentLLM
from app.services.agent.loop import AgentLoop
from app.services.agent.safety_gate import AgentSafetyGate
from app.services.agent.session import AgentSessionService
from app.services.agent.tools import TOOL_REGISTRY
from app.services.agent.types import (
    AgentTurnResult,
    TurnState,
)
from app.services.chat.output_guard import OutputGuard
from app.services.safety.escalation import build_escalator
from app.services.safety.ml_classifier import build_safety_classifier
from app.services.safety.rules import build_engine

_log = structlog.get_logger()


class AgentNavigator:
    """The care navigator, gate to action log."""

    def __init__(
        self,
        *,
        settings: Settings,
        gate: AgentSafetyGate,
        llm: AgentLLM,
        directory: DirectoryStore,
        sessions: AgentSessionService,
        output_guard: OutputGuard,
    ) -> None:
        self._settings = settings
        self._gate = gate
        self._llm = llm
        self._directory = directory
        self._sessions = sessions
        self._output_guard = output_guard
        self._loop = AgentLoop(
            llm,
            directory,
            output_guard=output_guard,
            max_steps=settings.agent_max_steps,
            token_budget=settings.agent_token_budget,
        )

    @property
    def tools(self) -> dict[str, Any]:
        """The allow-list, as the UI renders it."""
        return {name: spec.model_dump() for name, spec in TOOL_REGISTRY.items()}

    def open_session(self, user_id: str) -> TurnState:
        """A new navigator session owned by ``user_id``."""
        return self._sessions.open(user_id)

    async def chat(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        region: str | None = None,
        locale: str | None = None,
    ) -> AgentTurnResult:
        """Answer one navigator message, gate first."""
        state = self._owned_state(session_id, user_id)
        state.region = region or state.region
        state.locale = locale or state.locale
        outcome = await self._gate.decide(
            message, language=locale, region=state.region, locale=state.locale
        )
        if outcome.blocked:
            return self._crisis_turn(state, outcome)
        return await self._loop.run(state, message)

    async def confirm(
        self,
        *,
        user_id: str,
        session_id: str,
        pending_id: str,
        decision: str,
    ) -> AgentTurnResult:
        """Answer a confirmation card and resume the turn."""
        state = self._owned_state(session_id, user_id)
        return await self._loop.resume(state, pending_id, decision)

    def _owned_state(self, session_id: str, user_id: str) -> TurnState:
        state = self._sessions.get(session_id, user_id)
        if state is None:
            raise UnknownAgentSession()
        return state

    def _crisis_turn(self, state: TurnState, outcome: Any) -> AgentTurnResult:
        log = ActionLog()
        log.safety_gate_blocked()
        state.log = log.entries
        return AgentTurnResult(
            reply=outcome.crisis_reply
            or (
                "This sounds like it needs a person, not an app. "
                "Please call your local emergency number or a "
                "crisis helpline now."
            ),
            response_type="crisis",
            risk_level=outcome.risk_level.label,
            action_log=log.entries,
            crisis_resources=outcome.crisis_resources,
            steps_used=0,
            tokens_used=0,
        )


class UnknownAgentSession(Exception):  # noqa: N818
    """The session does not exist or belongs to somebody else."""


def build_agent_navigator(
    settings: Settings,
    *,
    llm: AgentLLM | None = None,
    output_guard: OutputGuard | None = None,
) -> AgentNavigator:
    """Build the navigator this deployment should use.

    Nothing here makes a network call. The gate reuses the
    shared rules engine, classifier and escalator — the same
    verdicts the chat pipeline would give. The model is the
    function-calling adapter when an Anthropic key and model
    are configured, and the deterministic Fake otherwise (the
    offline default, so a deployment without keys degrades to
    scripted replies rather than failing open).
    """
    if llm is None:
        if settings.agent_llm_model or settings.anthropic_api_key:
            from app.services.agent.llm import AnthropicFunctionLLM

            llm = AnthropicFunctionLLM(
                api_key=settings.anthropic_api_key or "",
                model=settings.agent_llm_model or settings.anthropic_model or "",
            )
        else:
            _log.warning(
                "agent.no_llm_configured",
                note=(
                    "agent_llm_model/anthropic_api_key unset; the "
                    "navigator answers from its deterministic fake"
                ),
            )
            llm = FakeAgentLLM()
    return AgentNavigator(
        settings=settings,
        gate=AgentSafetyGate(
            engine=build_engine(),
            classifier=build_safety_classifier(settings),
            escalator=build_escalator(),
            min_confidence=settings.safety_ml_min_confidence,
            crisis_mass_floor=settings.safety_ml_crisis_mass_floor,
            suspicion_floor=settings.safety_ml_suspicion_floor,
        ),
        llm=llm,
        directory=load_directory(),
        sessions=AgentSessionService(),
        output_guard=output_guard or _default_output_guard(),
    )


def _default_output_guard() -> OutputGuard:
    from app.services.safety.output_guard import RuleBasedOutputGuard

    return RuleBasedOutputGuard()
