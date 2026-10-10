"""The orchestrator pipeline, one guarantee at a time, over Fakes.

The headline test is ``test_high_risk_never_calls_the_llm``: the whole point of
the pipeline's ordering is that a HIGH or IMMINENT message is answered by the
pre-written response and the model is not involved. Everything else here pins
the other steps of the brief: redaction, emotion, the MEDIUM check-in, the
fallback when the model is down, and the ways each stage fails safely.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from structlog.testing import capture_logs

from app.core.errors import ApiError
from app.models.enums import RiskLevel as StoredRiskLevel
from app.models.enums import SafetyEventSource
from app.services.chat import (
    ChatOrchestrator,
    FinalEvent,
    GuardVerdict,
    ResponseType,
    RetrievedChunk,
    TokenEvent,
)
from app.services.chat.orchestrator import LAST_RESORT_CRISIS_TEXT
from app.services.chat.prompting import (
    CONTEXT_HEADER,
    HINT_LOW,
    HINT_MEDIUM,
    HINT_RECENT_CRISIS,
    HistoryTurn,
)
from app.services.llm.base import ProviderDown
from app.services.llm.canned import CANNED_REPLY, CannedProvider
from app.services.llm.chain import LLMChain
from app.services.llm.fake_provider import DEFAULT_REPLY, FakeLLMProvider
from app.services.nlp.fake import FakeEmotionAnalyzer
from app.services.nlp.redaction import Redactor
from app.services.safety.base import RiskLevel
from app.services.safety.escalation import build_escalator
from app.services.safety.rules import RuleEngine
from tests.chat.conftest import (
    HIGH_MESSAGE,
    IMMINENT_MESSAGE,
    MEDIUM_MESSAGE,
    NEUTRAL_MESSAGE,
    PII_MESSAGE,
    SAD_MESSAGE,
    FakeConversation,
)

Factory = Callable[..., ChatOrchestrator]


async def _reply(orch: ChatOrchestrator, convo: FakeConversation, text: str):  # type: ignore[no-untyped-def]
    admitted = orch.admit(user_id=convo.user_id, text=text, region="IN", locale="en")
    return await orch.respond(admitted, convo)


async def _stream(orch: ChatOrchestrator, convo: FakeConversation, text: str):  # type: ignore[no-untyped-def]
    admitted = orch.admit(user_id=convo.user_id, text=text, region="IN", locale="en")
    return [event async for event in orch.stream(admitted, convo)]


# --------------------------------------------------------------------------- #
# Step 2: HIGH / IMMINENT stop the pipeline                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", [HIGH_MESSAGE, IMMINENT_MESSAGE])
async def test_high_risk_never_calls_the_llm(
    make_orchestrator: Factory, conversation: FakeConversation, text: str
) -> None:
    llm = FakeLLMProvider()
    orch = make_orchestrator(llm)

    reply = await _reply(orch, conversation, text)

    assert llm.call_count == 0, "HIGH/IMMINENT must not reach the LLM"
    assert len(llm.stream_calls) == 0
    assert reply.metadata.response_type is ResponseType.CRISIS
    assert reply.metadata.risk_level in {"high", "imminent"}


@pytest.mark.parametrize("text", [HIGH_MESSAGE, IMMINENT_MESSAGE])
async def test_high_risk_never_calls_the_llm_when_streaming(
    make_orchestrator: Factory, conversation: FakeConversation, text: str
) -> None:
    llm = FakeLLMProvider()
    orch = make_orchestrator(llm)

    events = await _stream(orch, conversation, text)

    assert llm.call_count == 0
    assert len(llm.stream_calls) == 0
    assert isinstance(events[-1], FinalEvent)
    assert events[-1].reply.metadata.response_type is ResponseType.CRISIS


async def test_crisis_reply_is_the_prewritten_template_not_model_text(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider(replies=["MODEL IMPROVISATION"])
    reply = await _reply(make_orchestrator(llm), conversation, HIGH_MESSAGE)

    expected = build_escalator().plan(build_assessment(HIGH_MESSAGE), region="IN", locale="en")
    assert expected.message is not None
    assert reply.metadata.crisis == expected.message
    assert reply.reply.startswith(expected.message.title)
    assert "MODEL IMPROVISATION" not in reply.reply
    # The emergency instruction (with the regional number) is in the plain text.
    assert expected.message.emergency_instruction is not None
    assert expected.message.emergency_instruction in reply.reply


def build_assessment(text: str):  # type: ignore[no-untyped-def]
    from app.services.safety.rules import build_engine

    return build_engine().assess(text)


async def test_crisis_metadata_carries_everything_the_crisiscard_needs(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    reply = await _reply(make_orchestrator(), conversation, HIGH_MESSAGE)
    meta = reply.metadata

    assert meta.response_type is ResponseType.CRISIS
    assert meta.crisis is not None and meta.crisis.template_id.startswith("crisis.")
    assert meta.resources, "a crisis reply must carry helplines"
    assert meta.emergency is not None and meta.emergency.kind == "emergency"
    assert meta.region == "IN"
    assert meta.emotion is None, "the pipeline stops before emotion analysis"
    assert meta.degraded is False


async def test_crisis_persists_the_message_and_a_safety_event(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    reply = await _reply(make_orchestrator(), conversation, HIGH_MESSAGE)

    (user_turn,) = conversation.user_calls
    assert user_turn.text == HIGH_MESSAGE
    assert user_turn.risk == StoredRiskLevel.CRISIS
    assert user_turn.safety_event is SafetyEventSource.RULES
    (assistant_turn,) = conversation.assistant_calls
    assert assistant_turn.text == reply.reply
    assert assistant_turn.risk == StoredRiskLevel.CRISIS
    assert reply.metadata.persisted is True
    assert reply.message_id is not None


async def test_crisis_is_delivered_even_when_persistence_fails(
    make_orchestrator: Factory,
) -> None:
    broken = FakeConversation(fail_stores=True)
    llm = FakeLLMProvider()

    reply = await _reply(make_orchestrator(llm), broken, HIGH_MESSAGE)

    assert reply.metadata.response_type is ResponseType.CRISIS
    assert reply.metadata.persisted is False
    assert reply.metadata.resources
    assert llm.call_count == 0


async def test_crisis_with_a_missing_template_still_says_something(
    make_orchestrator: Factory, conversation: FakeConversation, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = make_orchestrator()
    real_plan = orch._escalator.plan

    def no_message(*args, **kwargs):  # type: ignore[no-untyped-def]
        return real_plan(*args, **kwargs).model_copy(update={"message": None})

    monkeypatch.setattr(orch._escalator, "plan", no_message)
    reply = await _reply(orch, conversation, HIGH_MESSAGE)

    assert reply.reply == LAST_RESORT_CRISIS_TEXT
    assert reply.metadata.response_type is ResponseType.CRISIS


async def test_ml_raise_to_high_also_stops_the_llm(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    """The ensemble can raise a rules-NONE message to HIGH; the gate must honour it."""
    from app.services.safety.ml_classifier import MLPrediction

    class AlwaysHigh:
        enabled = True
        version = "test"

        def predict(self, text: str) -> MLPrediction:
            return MLPrediction(
                level=RiskLevel.HIGH,
                probabilities={
                    "none": 0.02,
                    "low": 0.02,
                    "medium": 0.02,
                    "high": 0.9,
                    "imminent": 0.04,
                },
                confidence=0.9,
                version="test",
            )

    llm = FakeLLMProvider()
    orch = make_orchestrator(llm, classifier=AlwaysHigh())

    reply = await _reply(orch, conversation, NEUTRAL_MESSAGE)

    assert llm.call_count == 0
    assert reply.metadata.response_type is ResponseType.CRISIS
    assert conversation.user_calls[0].safety_event is SafetyEventSource.ML


async def test_a_broken_safety_stage_fails_closed(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    class BrokenEngine(RuleEngine):
        def assess(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("pattern file corrupt")

    llm = FakeLLMProvider()
    orch = make_orchestrator(llm, engine=BrokenEngine())
    with pytest.raises(ApiError) as caught:
        await _reply(orch, conversation, NEUTRAL_MESSAGE)

    assert caught.value.status_code == 503
    assert caught.value.code == "safety_unavailable"
    assert llm.call_count == 0, "no safety verdict means no model call"
    assert conversation.calls == []


# --------------------------------------------------------------------------- #
# Normal turns: NONE / LOW                                                      #
# --------------------------------------------------------------------------- #


async def test_a_neutral_message_is_a_normal_reply(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider()
    reply = await _reply(make_orchestrator(llm), conversation, NEUTRAL_MESSAGE)

    assert llm.call_count == 1
    assert reply.reply == DEFAULT_REPLY
    assert reply.metadata.response_type is ResponseType.NORMAL
    assert reply.metadata.risk_level == "none"
    assert reply.metadata.resources == []
    assert reply.metadata.check_in is None
    assert reply.metadata.crisis is None
    assert reply.metadata.degraded is False


async def test_both_turns_are_stored_with_risk_and_emotion(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    reply = await _reply(make_orchestrator(), conversation, SAD_MESSAGE)

    (user_turn,) = conversation.user_calls
    assert user_turn.text == SAD_MESSAGE, "the ORIGINAL text is stored, not the redacted one"
    assert user_turn.risk == StoredRiskLevel.NONE
    assert user_turn.emotion == reply.metadata.emotion == "sadness"
    assert user_turn.safety_event is None, "NONE writes no safety event"
    (assistant_turn,) = conversation.assistant_calls
    assert assistant_turn.text == reply.reply
    assert reply.metadata.persisted is True


async def test_emotion_is_analysed_from_the_original_text_not_the_redacted_one(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    analyzer = FakeEmotionAnalyzer()
    orch = make_orchestrator(analyzer=analyzer)

    await _reply(orch, conversation, PII_MESSAGE)

    (analysed, _lang) = analyzer.calls[0]
    assert "jo.sharma@example.com" in analysed
    assert "9876543210" in analysed


async def test_a_sad_message_gets_an_emotion_hint_in_the_system_prompt(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider()
    await _reply(make_orchestrator(llm), conversation, SAD_MESSAGE)

    call = llm.last_call
    assert call is not None and call.system is not None
    assert "Emotion hint" in call.system
    assert "sadness" in call.system
    assert CONTEXT_HEADER in call.system


async def test_a_neutral_message_gets_no_hints_at_all(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider()
    # The Fake analyzer reads a message with none of its keywords as neutral.
    orch = make_orchestrator(llm, analyzer=FakeEmotionAnalyzer())
    await _reply(orch, conversation, NEUTRAL_MESSAGE)

    call = llm.last_call
    assert call is not None and call.system is not None
    assert "Emotion hint" not in call.system
    assert "Safety hint" not in call.system
    assert CONTEXT_HEADER not in call.system


async def test_a_failing_emotion_analyzer_never_blocks_the_reply(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider()
    orch = make_orchestrator(llm, analyzer=FakeEmotionAnalyzer(error=RuntimeError("boom")))

    reply = await _reply(orch, conversation, SAD_MESSAGE)

    assert llm.call_count == 1
    assert reply.metadata.emotion is None
    assert reply.metadata.response_type is ResponseType.NORMAL


# --------------------------------------------------------------------------- #
# MEDIUM: the check-in                                                          #
# --------------------------------------------------------------------------- #


async def test_medium_risk_response_contains_a_check_in(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider()
    reply = await _reply(make_orchestrator(llm), conversation, MEDIUM_MESSAGE)

    template = reply.metadata.check_in
    assert template is not None and template.template_id == "check_in.medium"
    # The model's text first, then the reviewed template's offer — even though the
    # Fake model ignored the hint entirely.
    assert reply.reply == DEFAULT_REPLY + "\n\n" + template.body[-1]
    assert "helplines" in reply.reply
    assert reply.metadata.response_type is ResponseType.CHECK_IN
    assert reply.metadata.risk_level == "medium"
    assert reply.metadata.resources, "MEDIUM carries the region's helplines"
    assert reply.metadata.crisis is None
    assert reply.metadata.emergency is None


async def test_medium_risk_prompt_asks_the_model_for_a_check_in(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider()
    await _reply(make_orchestrator(llm), conversation, MEDIUM_MESSAGE)

    call = llm.last_call
    assert call is not None and call.system is not None
    assert HINT_MEDIUM in call.system
    assert "MEDIUM risk" in call.system
    assert "gentle check-in" in call.system


async def test_medium_risk_writes_a_safety_event_and_the_model_is_still_called(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider()
    await _reply(make_orchestrator(llm), conversation, MEDIUM_MESSAGE)

    assert llm.call_count == 1
    (user_turn,) = conversation.user_calls
    assert user_turn.risk == StoredRiskLevel.ELEVATED
    assert user_turn.safety_event is SafetyEventSource.RULES
    # The stored assistant text includes the check-in the client was shown.
    assert "helplines" in conversation.assistant_calls[0].text


async def test_the_check_in_is_the_last_body_paragraph_of_the_reviewed_template() -> None:
    """Pins the coupling: editing check_in.medium changes what is appended."""
    plan = build_escalator().plan(build_assessment(MEDIUM_MESSAGE), region="IN", locale="en")
    assert plan.message is not None
    assert plan.message.template_id == "check_in.medium"
    assert plan.message.body[-1].startswith("We can keep talking.")


# --------------------------------------------------------------------------- #
# Step 3: redaction                                                             #
# --------------------------------------------------------------------------- #


async def test_pii_is_redacted_before_the_llm_sees_it(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider()
    await _reply(make_orchestrator(llm), conversation, PII_MESSAGE)

    call = llm.last_call
    assert call is not None
    sent = call.all_text
    assert "jo.sharma@example.com" not in sent
    assert "9876543210" not in sent
    assert "[EMAIL]" in call.messages[-1].content
    assert "[PHONE]" in call.messages[-1].content


async def test_history_is_redacted_too(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    conversation.scripted_history = [
        HistoryTurn("user", "my email is old.address@example.org"),
        HistoryTurn("assistant", "Thanks for sharing that."),
    ]
    llm = FakeLLMProvider()
    await _reply(make_orchestrator(llm), conversation, NEUTRAL_MESSAGE)

    call = llm.last_call
    assert call is not None
    assert "old.address@example.org" not in call.all_text
    assert any("[EMAIL]" in m.content for m in call.messages)


async def test_the_stored_message_keeps_the_original_even_though_the_model_got_placeholders(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    await _reply(make_orchestrator(), conversation, PII_MESSAGE)
    assert conversation.user_calls[0].text == PII_MESSAGE


async def test_the_users_identity_never_reaches_the_model(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider()
    await _reply(make_orchestrator(llm), conversation, SAD_MESSAGE)

    call = llm.last_call
    assert call is not None
    haystack = (call.system or "") + call.all_text
    assert str(conversation.user_id) not in haystack
    assert str(conversation.session_id) not in haystack


async def test_if_redaction_fails_the_model_is_not_called(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    class BrokenRedactor(Redactor):
        def redact(self, text: str):  # type: ignore[no-untyped-def]
            raise RuntimeError("regex engine failed")

    llm = FakeLLMProvider()
    reply = await _reply(
        make_orchestrator(llm, redactor=BrokenRedactor()), conversation, SAD_MESSAGE
    )

    assert llm.call_count == 0, "unredacted text must never be sent"
    assert reply.metadata.response_type is ResponseType.FALLBACK
    assert reply.reply == CANNED_REPLY


async def test_redaction_also_holds_through_the_real_provider_chain(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    """Double redaction (orchestrator then chain) is idempotent, not corrupting."""
    fake = FakeLLMProvider()
    chain = LLMChain([fake, CannedProvider()], redactor=Redactor())
    await _reply(make_orchestrator(chain), conversation, PII_MESSAGE)

    call = fake.last_call
    assert call is not None
    assert "jo.sharma@example.com" not in call.all_text
    assert call.messages[-1].content.count("[EMAIL]") == 1


# --------------------------------------------------------------------------- #
# Step 5: retrieval                                                             #
# --------------------------------------------------------------------------- #


async def test_the_retrieval_stub_adds_nothing_to_the_prompt(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider()
    await _reply(make_orchestrator(llm), conversation, NEUTRAL_MESSAGE)

    call = llm.last_call
    assert call is not None and call.system is not None
    assert "Reference material" not in call.system


async def test_retrieved_passages_are_framed_as_reference_not_as_the_user(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    queries: list[str] = []

    class OneChunk:
        async def retrieve(self, query: str, *, limit: int = 3) -> list[RetrievedChunk]:
            queries.append(query)
            return [RetrievedChunk("c1", "Box breathing", "Breathe in for four, out for four.")]

    llm = FakeLLMProvider()
    await _reply(make_orchestrator(llm, retriever=OneChunk()), conversation, PII_MESSAGE)

    call = llm.last_call
    assert call is not None and call.system is not None
    assert "Reference material" in call.system
    assert "Box breathing: Breathe in for four, out for four." in call.system
    assert queries and "[EMAIL]" in queries[0], "retrieval gets the REDACTED query"
    assert "jo.sharma@example.com" not in queries[0]


async def test_a_failing_retriever_degrades_to_no_context(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    class Broken:
        async def retrieve(self, query: str, *, limit: int = 3) -> list[RetrievedChunk]:
            raise RuntimeError("vector store down")

    llm = FakeLLMProvider()
    reply = await _reply(make_orchestrator(llm, retriever=Broken()), conversation, SAD_MESSAGE)

    assert llm.call_count == 1
    assert reply.metadata.response_type is ResponseType.NORMAL


# --------------------------------------------------------------------------- #
# Step 6: the window                                                            #
# --------------------------------------------------------------------------- #


async def test_the_window_carries_prior_turns_in_order_ending_on_the_new_message(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    conversation.scripted_history = [
        HistoryTurn("user", "first thing"),
        HistoryTurn("assistant", "first answer"),
        HistoryTurn("user", "second thing"),
        HistoryTurn("assistant", "second answer"),
    ]
    llm = FakeLLMProvider()
    await _reply(make_orchestrator(llm), conversation, NEUTRAL_MESSAGE)

    call = llm.last_call
    assert call is not None
    assert [m.content for m in call.messages] == [
        "first thing",
        "first answer",
        "second thing",
        "second answer",
        NEUTRAL_MESSAGE,
    ]
    assert [m.role for m in call.messages] == ["user", "assistant", "user", "assistant", "user"]


async def test_the_window_is_short(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    conversation.scripted_history = [
        HistoryTurn("user" if i % 2 == 0 else "assistant", f"turn {i}") for i in range(30)
    ]
    llm = FakeLLMProvider()
    await _reply(make_orchestrator(llm, history_turns=4), conversation, NEUTRAL_MESSAGE)

    call = llm.last_call
    assert call is not None
    expected = ["turn 26", "turn 27", "turn 28", "turn 29", NEUTRAL_MESSAGE]
    assert [m.content for m in call.messages] == expected[-len(call.messages) :]
    assert len(call.messages) <= 5


async def test_the_window_never_starts_on_an_assistant_turn(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    conversation.scripted_history = [
        HistoryTurn("assistant", "orphaned answer"),
        HistoryTurn("user", "a question"),
        HistoryTurn("assistant", "an answer"),
    ]
    llm = FakeLLMProvider()
    await _reply(make_orchestrator(llm), conversation, NEUTRAL_MESSAGE)

    call = llm.last_call
    assert call is not None and call.messages[0].role == "user"
    assert "orphaned answer" not in call.all_text


async def test_a_crisis_exchange_is_not_replayed_to_the_model_on_the_next_turn(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    conversation.scripted_history = [
        HistoryTurn("user", "ordinary earlier message"),
        HistoryTurn("assistant", "ordinary earlier answer"),
        HistoryTurn("user", "THE CRISIS DISCLOSURE", crisis=True),
        HistoryTurn("assistant", "THE CRISIS TEMPLATE", crisis=True),
    ]
    llm = FakeLLMProvider()
    await _reply(make_orchestrator(llm), conversation, "ok thanks")

    call = llm.last_call
    assert call is not None and call.system is not None
    assert "THE CRISIS DISCLOSURE" not in call.all_text
    assert "THE CRISIS TEMPLATE" not in call.all_text
    assert "ordinary earlier message" in call.all_text
    assert HINT_RECENT_CRISIS in call.system


async def test_low_risk_hint_text_is_used_for_the_low_tier(
    make_orchestrator: Factory, conversation: FakeConversation, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.safety.base import RiskAssessment

    class LowEngine(RuleEngine):
        def assess(self, text: str, *args, **kwargs) -> RiskAssessment:  # type: ignore[no-untyped-def]
            return RiskAssessment(level=RiskLevel.LOW)

    llm = FakeLLMProvider()
    reply = await _reply(make_orchestrator(llm, engine=LowEngine()), conversation, SAD_MESSAGE)

    call = llm.last_call
    assert call is not None and call.system is not None
    assert HINT_LOW in call.system
    assert reply.metadata.response_type is ResponseType.NORMAL
    assert reply.metadata.risk_level == "low"
    assert reply.metadata.resources == []


# --------------------------------------------------------------------------- #
# Step 7: LLM outage -> fallback                                                #
# --------------------------------------------------------------------------- #


async def test_an_llm_outage_returns_the_fallback_template_not_an_error(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider(error=ProviderDown("provider is down"))
    reply = await _reply(make_orchestrator(llm), conversation, SAD_MESSAGE)

    assert llm.call_count == 1
    assert reply.reply == CANNED_REPLY
    assert reply.metadata.response_type is ResponseType.FALLBACK
    assert reply.metadata.degraded is True
    # The conversation is still recorded.
    assert conversation.assistant_calls[0].text == CANNED_REPLY


async def test_an_unexpected_exception_from_the_provider_is_also_a_fallback(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider(error=ValueError("SDK bug"))
    reply = await _reply(make_orchestrator(llm), conversation, SAD_MESSAGE)
    assert reply.metadata.response_type is ResponseType.FALLBACK


async def test_the_real_chain_with_a_dead_primary_degrades_to_the_canned_reply(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    dead = FakeLLMProvider(error=ProviderDown("down"))
    chain = LLMChain([dead, CannedProvider()], redactor=Redactor())
    reply = await _reply(make_orchestrator(chain), conversation, SAD_MESSAGE)

    assert reply.reply == CANNED_REPLY
    assert reply.metadata.response_type is ResponseType.FALLBACK
    assert reply.metadata.degraded is True


async def test_an_empty_model_reply_is_treated_as_an_outage(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider(replies=["   "])
    reply = await _reply(make_orchestrator(llm), conversation, SAD_MESSAGE)
    assert reply.reply == CANNED_REPLY


async def test_a_fallback_at_medium_still_carries_the_check_in_and_helplines(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider(error=ProviderDown("down"))
    reply = await _reply(make_orchestrator(llm), conversation, MEDIUM_MESSAGE)

    assert reply.metadata.response_type is ResponseType.FALLBACK
    assert reply.reply.startswith(CANNED_REPLY)
    assert "helplines" in reply.reply
    assert reply.metadata.resources
    assert reply.metadata.check_in is not None


async def test_a_failed_assistant_write_still_returns_the_reply(
    make_orchestrator: Factory,
) -> None:
    class FailsOnAssistant(FakeConversation):
        async def store_assistant_turn(self, text, *, risk):  # type: ignore[no-untyped-def]
            raise RuntimeError("write failed")

    convo = FailsOnAssistant()
    reply = await _reply(make_orchestrator(), convo, SAD_MESSAGE)
    assert reply.reply == DEFAULT_REPLY
    assert reply.metadata.persisted is False
    assert reply.message_id is None


# --------------------------------------------------------------------------- #
# Step 8: the output guard                                                      #
# --------------------------------------------------------------------------- #


class _Guard:
    def __init__(self, *, verdict: GuardVerdict | None = None, full: bool = False) -> None:
        self.requires_full_text = full
        self._verdict = verdict
        self.seen: list[str] = []

    async def check(self, text: str, *, risk: RiskLevel) -> GuardVerdict:
        self.seen.append(text)
        return self._verdict or GuardVerdict(text=text)


async def test_the_stub_guard_passes_text_through_unchanged(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    reply = await _reply(make_orchestrator(), conversation, NEUTRAL_MESSAGE)
    assert reply.reply == DEFAULT_REPLY


async def test_every_model_reply_goes_through_the_guard(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    guard = _Guard()
    await _reply(make_orchestrator(output_guard=guard), conversation, NEUTRAL_MESSAGE)
    assert guard.seen == [DEFAULT_REPLY]


async def test_a_guard_replacement_becomes_a_fallback_response(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    guard = _Guard(verdict=GuardVerdict(text="A SAFE REPLACEMENT", passed=False, reason="test"))
    reply = await _reply(make_orchestrator(output_guard=guard), conversation, NEUTRAL_MESSAGE)

    assert reply.reply == "A SAFE REPLACEMENT"
    assert reply.metadata.response_type is ResponseType.FALLBACK
    assert DEFAULT_REPLY not in conversation.assistant_calls[0].text


async def test_a_crashing_guard_fails_closed(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    class Crashing:
        requires_full_text = False

        async def check(self, text: str, *, risk: RiskLevel) -> GuardVerdict:
            raise RuntimeError("guard crashed")

    reply = await _reply(make_orchestrator(output_guard=Crashing()), conversation, NEUTRAL_MESSAGE)
    assert reply.reply == CANNED_REPLY, "an unchecked reply must not get through"
    assert reply.metadata.response_type is ResponseType.FALLBACK


async def test_the_crisis_template_does_not_go_through_the_guard(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    guard = _Guard()
    await _reply(make_orchestrator(output_guard=guard), conversation, HIGH_MESSAGE)
    assert guard.seen == []


# --------------------------------------------------------------------------- #
# Streaming                                                                     #
# --------------------------------------------------------------------------- #


async def test_a_stream_is_tokens_in_order_then_one_final_event(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider(chunks=["I hear ", "you. ", "That sounds ", "hard."])
    events = await _stream(make_orchestrator(llm), conversation, SAD_MESSAGE)

    tokens = [e.text for e in events[:-1] if isinstance(e, TokenEvent)]
    assert tokens == ["I hear ", "you. ", "That sounds ", "hard."]
    assert len(tokens) == len(events) - 1, "only tokens before the final event"
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert final.reply.reply == "".join(tokens)
    assert final.replaced is False
    assert final.reply.metadata.response_type is ResponseType.NORMAL
    assert llm.call_count == 0 and len(llm.stream_calls) == 1


async def test_a_stream_at_medium_appends_the_check_in_as_a_token(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    events = await _stream(make_orchestrator(), conversation, MEDIUM_MESSAGE)

    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert "".join(e.text for e in events if isinstance(e, TokenEvent)) == final.reply.reply
    assert "helplines" in final.reply.reply
    assert final.reply.metadata.response_type is ResponseType.CHECK_IN


async def test_a_crisis_stream_is_one_token_then_final(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    events = await _stream(make_orchestrator(), conversation, HIGH_MESSAGE)

    assert len(events) == 2
    assert isinstance(events[0], TokenEvent) and isinstance(events[1], FinalEvent)
    assert events[0].text == events[1].reply.reply


async def test_a_stream_with_a_dead_provider_sends_the_fallback_as_tokens(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider(error=ProviderDown("down"))
    events = await _stream(make_orchestrator(llm), conversation, SAD_MESSAGE)

    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert "".join(e.text for e in events[:-1] if isinstance(e, TokenEvent)) == CANNED_REPLY
    assert final.reply.metadata.response_type is ResponseType.FALLBACK
    assert final.replaced is False


async def test_a_failure_mid_stream_replaces_what_was_sent(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    class DiesMidStream(FakeLLMProvider):
        async def stream(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            yield "Half a "
            raise ProviderDown("connection reset")

    events = await _stream(make_orchestrator(DiesMidStream()), conversation, SAD_MESSAGE)

    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert final.replaced is True, "the client must swap its partial text"
    assert final.reply.reply == CANNED_REPLY
    assert final.reply.metadata.response_type is ResponseType.FALLBACK


async def test_a_guard_rewrite_after_live_tokens_is_flagged_as_replaced(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    guard = _Guard(verdict=GuardVerdict(text="rewritten", passed=False))
    events = await _stream(make_orchestrator(output_guard=guard), conversation, SAD_MESSAGE)

    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert final.replaced is True
    assert final.reply.reply == "rewritten"


async def test_a_full_text_guard_buffers_so_unchecked_text_is_never_sent(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    llm = FakeLLMProvider(replies=["UNSAFE MODEL OUTPUT that should never be seen"])
    guard = _Guard(verdict=GuardVerdict(text="A safe replacement.", passed=False), full=True)
    events = await _stream(make_orchestrator(llm, output_guard=guard), conversation, SAD_MESSAGE)

    sent = "".join(e.text for e in events if isinstance(e, TokenEvent))
    assert "UNSAFE" not in sent
    assert sent == "A safe replacement."
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert final.replaced is False, "nothing unchecked was ever shown, so nothing to replace"
    assert guard.seen == ["UNSAFE MODEL OUTPUT that should never be seen"]


# --------------------------------------------------------------------------- #
# Step 1: validation and rate limiting                                          #
# --------------------------------------------------------------------------- #


def _admit_error(orch: ChatOrchestrator, text: str) -> ApiError:
    with pytest.raises(ApiError) as caught:
        orch.admit(user_id=uuid.uuid4(), text=text)
    return caught.value


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "message_empty"),
        ("   \n\t  ", "message_empty"),
        ("x" * 101, "message_too_long"),
        ("hello\x00world", "message_encoding"),
        ("hello\x1bworld", "message_encoding"),
        ("bad \ud800 surrogate", "message_encoding"),
    ],
)
def test_invalid_messages_are_rejected_with_a_stable_code(
    make_orchestrator: Factory, text: str, code: str
) -> None:
    error = _admit_error(make_orchestrator(max_message_chars=100), text)
    assert error.status_code == 422
    assert error.code == code
    assert text.strip() not in error.message or not text.strip(), "the input is never echoed"


def test_a_message_at_the_limit_is_accepted(make_orchestrator: Factory) -> None:
    orch = make_orchestrator(max_message_chars=100)
    admitted = orch.admit(user_id=uuid.uuid4(), text="x" * 100)
    assert len(admitted.text) == 100


def test_newlines_tabs_and_non_latin_scripts_are_fine(make_orchestrator: Factory) -> None:
    orch = make_orchestrator()
    text = "आज बहुत थका हुआ हूँ\nপ্রায় সব কিছু\tভারী লাগছে 😔"
    assert orch.admit(user_id=uuid.uuid4(), text=text).text == text


def test_text_is_normalised_to_nfc(make_orchestrator: Factory) -> None:
    orch = make_orchestrator()
    decomposed = "cafe\u0301"  # e + combining acute
    assert orch.admit(user_id=uuid.uuid4(), text=decomposed).text == "caf\u00e9"


def test_the_rate_limit_is_per_user_and_carries_retry_after(make_orchestrator: Factory) -> None:
    orch = make_orchestrator(rate_limit_per_minute=3)
    alice, bob = uuid.uuid4(), uuid.uuid4()
    for _ in range(3):
        orch.admit(user_id=alice, text="hello")

    with pytest.raises(ApiError) as caught:
        orch.admit(user_id=alice, text="hello")

    assert caught.value.status_code == 429
    assert caught.value.code == "rate_limited"
    assert caught.value.headers is not None and int(caught.value.headers["Retry-After"]) >= 1
    orch.admit(user_id=bob, text="hello")  # another person's budget is untouched


def test_an_invalid_message_costs_no_rate_limit_budget(make_orchestrator: Factory) -> None:
    orch = make_orchestrator(rate_limit_per_minute=1)
    user = uuid.uuid4()
    for _ in range(5):
        with pytest.raises(ApiError):
            orch.admit(user_id=user, text="")
    orch.admit(user_id=user, text="a real message")  # still has its one


def test_rate_limiting_can_be_switched_off(make_orchestrator: Factory) -> None:
    orch = make_orchestrator(rate_limit_per_minute=1, rate_limit_enabled=False)
    user = uuid.uuid4()
    for _ in range(10):
        orch.admit(user_id=user, text="hello")


# --------------------------------------------------------------------------- #
# Privacy: the log                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", [SAD_MESSAGE, MEDIUM_MESSAGE, HIGH_MESSAGE, PII_MESSAGE])
async def test_no_log_line_contains_the_text_or_the_reply(
    make_orchestrator: Factory, conversation: FakeConversation, text: str
) -> None:
    with capture_logs() as captured:
        reply = await _reply(make_orchestrator(), conversation, text)

    assert captured, "the turn must log something"
    blob = repr(captured)
    assert text not in blob
    assert reply.reply not in blob
    assert "jo.sharma" not in blob
    assert str(conversation.user_id) not in blob
    turn = next(line for line in captured if line["event"] == "chat_turn")
    assert turn["text_length"] == len(text)
    assert len(turn["text_sha"]) == 16
    assert turn["llm_called"] is (reply.metadata.response_type is not ResponseType.CRISIS)


async def test_a_failed_safety_stage_logs_no_text_either(
    make_orchestrator: Factory, conversation: FakeConversation
) -> None:
    class BrokenEngine(RuleEngine):
        def assess(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError(f"cannot parse {NEUTRAL_MESSAGE}")

    with capture_logs() as captured, pytest.raises(ApiError):
        await _reply(make_orchestrator(engine=BrokenEngine()), conversation, NEUTRAL_MESSAGE)

    assert NEUTRAL_MESSAGE not in repr(captured), "an exception's text can echo the input"


# --------------------------------------------------------------------------- #
# Consistency with the Day 8 / 9 assess endpoint                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "level"),
    [
        (NEUTRAL_MESSAGE, "none"),
        (MEDIUM_MESSAGE, "medium"),
        (HIGH_MESSAGE, "high"),
        (IMMINENT_MESSAGE, "imminent"),
    ],
)
async def test_risk_level_matches_the_public_assess_endpoint(
    make_orchestrator: Factory, conversation: FakeConversation, text: str, level: str
) -> None:
    from app.services.safety.ml_classifier import NullClassifier
    from app.services.safety.pipeline import run_ensemble
    from app.services.safety.rules import build_engine

    assessment, _ = await run_ensemble(
        text,
        None,
        build_engine(),
        NullClassifier(reason="disabled"),
        min_confidence=0.7,
        crisis_mass_floor=0.3,
        suspicion_floor=0.25,
    )
    reply = await _reply(make_orchestrator(), conversation, text)

    assert reply.metadata.risk_level == assessment.level.label == level
