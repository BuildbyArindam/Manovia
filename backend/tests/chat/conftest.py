"""Shared fixtures for the chat tests.

Two layers of test live in this directory and they share these helpers:

* **orchestrator tests** drive :class:`ChatOrchestrator` directly with a
  :class:`FakeConversation` (records every store call, touches no database) and a
  scripted :class:`FakeLLMProvider`, so each pipeline step is asserted in
  isolation;
* **API tests** drive the real FastAPI app over HTTP with the Fake provider
  installed on ``app.state``.

Everything is offline and deterministic. The messages below are synthetic and
were checked against the shipped rules engine: each lands on the tier its name
says (``tests/chat/test_fixtures.py`` re-asserts that, so a change to the rule
patterns that moves one of them fails loudly *here* rather than as a puzzling
orchestrator failure).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.models.enums import RiskLevel as StoredRiskLevel
from app.models.enums import SafetyEventSource
from app.services.chat import ChatOrchestrator, OrchestratorConfig
from app.services.chat.output_guard import OutputGuard
from app.services.chat.prompting import HistoryTurn
from app.services.chat.retrieval import Retriever
from app.services.llm.base import LLMProvider
from app.services.llm.fake_provider import FakeLLMProvider
from app.services.nlp.base import EmotionAnalyzer
from app.services.nlp.keyword import KeywordFallbackAnalyzer
from app.services.nlp.redaction import Redactor
from app.services.safety.escalation import build_escalator
from app.services.safety.ml_classifier import NullClassifier, SafetyClassifier
from app.services.safety.rules import RuleEngine, build_engine

# Pinned against the real rules engine (see test_fixtures.py).
NEUTRAL_MESSAGE = "What's a good way to plan my study schedule?"
SAD_MESSAGE = "I'm feeling really sad and tired today"
MEDIUM_MESSAGE = "I feel so hopeless and nothing will ever change"
HIGH_MESSAGE = "I want to kill myself"
IMMINENT_MESSAGE = "I'm going to end my life tonight"
PII_MESSAGE = "I feel sad. Call me on 9876543210 or write to jo.sharma@example.com"


@dataclass
class StoredCall:
    """One ``store_*_turn`` call, as the FakeConversation saw it."""

    kind: str  # "user" | "assistant"
    text: str
    risk: StoredRiskLevel
    emotion: str | None = None
    safety_event: SafetyEventSource | None = None


@dataclass
class FakeConversation:
    """A Conversation that records writes and serves a scripted history."""

    session_id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    persistent: bool = True
    stores: bool = True
    scripted_history: list[HistoryTurn] = field(default_factory=list)
    fail_stores: bool = False
    calls: list[StoredCall] = field(default_factory=list)

    @property
    def stores_turns(self) -> bool:
        return self.stores

    async def history(self, limit: int) -> list[HistoryTurn]:
        return list(self.scripted_history[-limit:]) if limit > 0 else []

    async def store_user_turn(
        self,
        text: str,
        *,
        risk: StoredRiskLevel,
        emotion: str | None,
        safety_event: SafetyEventSource | None,
    ) -> uuid.UUID | None:
        if self.fail_stores:
            raise RuntimeError("database is down (test-simulated)")
        self.calls.append(StoredCall("user", text, risk, emotion, safety_event))
        return uuid.uuid4()

    async def store_assistant_turn(self, text: str, *, risk: StoredRiskLevel) -> uuid.UUID | None:
        if self.fail_stores:
            raise RuntimeError("database is down (test-simulated)")
        self.calls.append(StoredCall("assistant", text, risk))
        return uuid.uuid4()

    @property
    def user_calls(self) -> list[StoredCall]:
        return [c for c in self.calls if c.kind == "user"]

    @property
    def assistant_calls(self) -> list[StoredCall]:
        return [c for c in self.calls if c.kind == "assistant"]


@pytest.fixture
def conversation() -> FakeConversation:
    return FakeConversation()


@pytest.fixture
def fake_llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def make_orchestrator() -> Callable[..., ChatOrchestrator]:
    """Build an orchestrator over Fakes; override any collaborator by keyword."""

    def build(
        llm: LLMProvider | None = None,
        *,
        analyzer: EmotionAnalyzer | None = None,
        classifier: SafetyClassifier | None = None,
        engine: RuleEngine | None = None,
        redactor: Redactor | None = None,
        retriever: Retriever | None = None,
        output_guard: OutputGuard | None = None,
        rate_limit_per_minute: int = 1000,
        **config: Any,
    ) -> ChatOrchestrator:
        return ChatOrchestrator(
            llm=llm or FakeLLMProvider(),
            engine=engine or build_engine(),
            classifier=classifier or NullClassifier(reason="disabled"),
            escalator=build_escalator(),
            analyzer=analyzer or KeywordFallbackAnalyzer(),
            redactor=redactor or Redactor(),
            retriever=retriever,
            output_guard=output_guard,
            rate_limit_per_minute=rate_limit_per_minute,
            config=OrchestratorConfig(**config),
        )

    return build
