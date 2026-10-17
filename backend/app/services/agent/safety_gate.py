"""The gate: the agent sits BEHIND the chat safety pipeline.

Day 44 brief §4: at risk >= MEDIUM the agent does not run —
the deterministic crisis flow takes over. This module is
that rule, in one place, reusing the *same* ensemble the
chat orchestrator uses (rules engine + ML backstop,
:func:`app.services.safety.pipeline.run_ensemble`) and the
*same* escalator that writes crisis replies — so the agent
path and the chat path can never disagree about a verdict.

The gate runs on the person's raw message, before any
model call, exactly as AGENTS.md rule 1 requires. The
agent's own LLM is never constructed into a request when
the gate blocks: no prompt is built, no tokens spent, no
tool considered.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, Field

# The chat pipeline's own plain-text renderer for crisis
# templates, so the agent path and the chat path render a
# crisis reply identically. Private today; moving it into
# escalation.py is a refactor for another day.
from app.services.chat.orchestrator import (
    _plain_text,
)
from app.services.safety.base import RiskLevel
from app.services.safety.ensemble import apply_decision
from app.services.safety.escalation import Escalator
from app.services.safety.ml_classifier import SafetyClassifier
from app.services.safety.pipeline import run_ensemble
from app.services.safety.rules import RuleEngine

#: The level at which the agent stops and the crisis flow
#: takes over. The chat pipeline answers MEDIUM with a
#: check-in; the agent is stricter by design — navigating
#: support options is not worth the risk on a MEDIUM day.
AGENT_GATE_LEVEL: Final[RiskLevel] = RiskLevel.MEDIUM


class GateOutcome(BaseModel):
    """What the gate decided, as the API serves it."""

    allowed: bool
    risk_level: RiskLevel
    crisis_reply: str | None = None
    crisis_resources: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return not self.allowed


class AgentSafetyGate:
    """Crisis detection first; the agent only ever sees LOW."""

    def __init__(
        self,
        engine: RuleEngine,
        classifier: SafetyClassifier,
        escalator: Escalator,
        *,
        min_confidence: float = 0.5,
        crisis_mass_floor: float = 0.5,
        suspicion_floor: float = 0.5,
    ) -> None:
        self._engine = engine
        self._classifier = classifier
        self._escalator = escalator
        self._min_confidence = min_confidence
        self._crisis_mass_floor = crisis_mass_floor
        self._suspicion_floor = suspicion_floor

    async def decide(
        self,
        text: str,
        *,
        language: str | None = None,
        region: str | None = None,
        locale: str | None = None,
    ) -> GateOutcome:
        """The verdict for one inbound message.

        Returns either an allowed outcome (risk below
        MEDIUM — the agent may run) or a blocked outcome
        carrying the deterministic, pre-written reply and
        helplines.
        """
        assessment, decision = await run_ensemble(
            text,
            language,
            self._engine,
            self._classifier,
            min_confidence=self._min_confidence,
            crisis_mass_floor=self._crisis_mass_floor,
            suspicion_floor=self._suspicion_floor,
        )
        final = apply_decision(assessment, decision)
        if final.level >= AGENT_GATE_LEVEL:
            plan = self._escalator.plan(final, region=region, locale=locale)
            message = _plain_text(plan.message) if plan.message else None
            return GateOutcome(
                allowed=False,
                risk_level=final.level,
                crisis_reply=message,
                crisis_resources=[resource.model_dump() for resource in plan.resources],
            )
        return GateOutcome(allowed=True, risk_level=final.level)
