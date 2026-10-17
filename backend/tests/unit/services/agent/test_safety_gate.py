"""The gate: crisis detection before the agent.

The agent sits BEHIND the chat safety pipeline. At
risk >= MEDIUM it does not run at all — the
deterministic crisis flow answers — and the gate uses
the same engine, classifier and escalator as chat, so
the two paths cannot disagree.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.agent.safety_gate import (
    AGENT_GATE_LEVEL,
    AgentSafetyGate,
)
from app.services.safety.base import RiskLevel
from app.services.safety.escalation import build_escalator
from app.services.safety.ml_classifier import NullClassifier
from app.services.safety.rules import build_engine


def _gate() -> AgentSafetyGate:
    return AgentSafetyGate(
        engine=build_engine(),
        classifier=NullClassifier(),
        escalator=build_escalator(),
    )


def test_the_gate_level_is_medium_or_harder() -> None:
    assert AGENT_GATE_LEVEL >= RiskLevel.MEDIUM


@pytest.mark.parametrize(
    "text",
    [
        "I want to kill myself tonight",
        "i am going to end my life",
        "I just want to disappear sometimes",
    ],
)
def test_medium_and_above_never_reach_the_agent(text: str) -> None:
    outcome = asyncio.run(_gate().decide(text))
    assert not outcome.allowed
    assert outcome.risk_level >= AGENT_GATE_LEVEL
    # The gate answers with the deterministic crisis
    # reply and helplines — it does not hand the model
    # anything.
    assert outcome.crisis_reply
    assert outcome.crisis_resources


@pytest.mark.parametrize(
    "text",
    [
        "I feel a bit stressed about my exams",
        "Can you find low-cost Hindi-speaking support near me?",
        "What are the options for therapy?",
    ],
)
def test_everyday_messages_may_use_the_agent(text: str) -> None:
    outcome = asyncio.run(_gate().decide(text))
    assert outcome.allowed


def test_the_gate_reports_the_level_it_saw() -> None:
    outcome = asyncio.run(_gate().decide("I want to kill myself tonight"))
    assert outcome.risk_level == RiskLevel.HIGH
