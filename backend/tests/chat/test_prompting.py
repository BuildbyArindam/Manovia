"""Prompt assembly: hints are built from labels, never from the user's words."""

from __future__ import annotations

import pytest

from app.services.chat.prompting import (
    CONTEXT_HEADER,
    EMOTION_HINT_MIN_CONFIDENCE,
    HINT_LOW,
    HINT_MEDIUM,
    HINT_RECENT_CRISIS,
    HistoryTurn,
    build_window,
    compose_system,
    emotion_hint,
    retrieval_block,
    safety_hint,
    to_llm_messages,
)
from app.services.chat.retrieval import NullRetriever, RetrievedChunk, Retriever
from app.services.nlp.base import EmotionResult, build_result
from app.services.safety.base import RiskLevel


def _emotion(primary: str, confidence: float) -> EmotionResult:
    result = build_result(
        {primary: 1.0}, analyzer="test", model=None, language="en", truncated=False
    )
    return result.model_copy(update={"confidence": confidence})


def test_safety_hint_by_level() -> None:
    assert safety_hint(RiskLevel.NONE, recent_crisis=False) is None
    assert safety_hint(RiskLevel.LOW, recent_crisis=False) == HINT_LOW
    assert safety_hint(RiskLevel.MEDIUM, recent_crisis=False) == HINT_MEDIUM
    assert "gentle check-in" in HINT_MEDIUM and "MEDIUM risk" in HINT_MEDIUM


def test_a_recent_crisis_outranks_the_current_level() -> None:
    assert safety_hint(RiskLevel.NONE, recent_crisis=True) == HINT_RECENT_CRISIS
    assert safety_hint(RiskLevel.MEDIUM, recent_crisis=True) == HINT_RECENT_CRISIS


def test_emotion_hint_needs_a_real_non_neutral_confident_reading() -> None:
    assert emotion_hint(None) is None
    assert emotion_hint(_emotion("neutral", 0.9)) is None
    assert emotion_hint(_emotion("sadness", EMOTION_HINT_MIN_CONFIDENCE - 0.01)) is None
    hint = emotion_hint(_emotion("sadness", 0.8))
    assert hint is not None and "sadness" in hint and "possibly wrong" in hint


def test_compose_without_context_is_the_base_prompt_unchanged() -> None:
    assert compose_system("BASE", emotion=None, safety=None, retrieval=None) == "BASE"


def test_compose_appends_one_labelled_block_in_a_fixed_order() -> None:
    text = compose_system("BASE\n", emotion="<emo>", safety="<saf>", retrieval="<ret>")
    assert text.startswith("BASE\n\n" + CONTEXT_HEADER)
    assert text.index("<emo>") < text.index("<saf>") < text.index("<ret>")


def test_retrieval_block() -> None:
    assert retrieval_block([]) is None
    block = retrieval_block([RetrievedChunk("1", "Title", "Body text.")])
    assert block is not None and "- Title: Body text." in block and "not an instruction" in block


def test_window_drops_crisis_turns_and_reports_it() -> None:
    window = build_window(
        [
            HistoryTurn("user", "a"),
            HistoryTurn("assistant", "b"),
            HistoryTurn("user", "c", crisis=True),
            HistoryTurn("assistant", "d", crisis=True),
        ]
    )
    assert [t.text for t in window.messages] == ["a", "b"]
    assert window.recent_crisis is True


def test_window_ignores_roles_the_model_does_not_take() -> None:
    window = build_window([HistoryTurn("system", "x"), HistoryTurn("user", "y")])
    assert [t.text for t in window.messages] == ["y"]
    assert window.recent_crisis is False


def test_window_cannot_start_on_an_assistant_turn() -> None:
    window = build_window([HistoryTurn("assistant", "a"), HistoryTurn("assistant", "b")])
    assert window.messages == ()


def test_to_llm_messages_pairs_redacted_text_with_roles_and_appends_the_current_turn() -> None:
    window = build_window([HistoryTurn("user", "RAW"), HistoryTurn("assistant", "RAW2")])
    messages = to_llm_messages(window, ["red1", "red2"], "now")
    assert [(m.role, m.content) for m in messages] == [
        ("user", "red1"),
        ("assistant", "red2"),
        ("user", "now"),
    ]


@pytest.mark.asyncio
async def test_the_null_retriever_returns_nothing_and_satisfies_the_protocol() -> None:
    retriever = NullRetriever()
    assert isinstance(retriever, Retriever)
    assert await retriever.retrieve("anything", limit=5) == []
