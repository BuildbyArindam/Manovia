"""The token/cost guard: truncation, windowing and the output budget.

Two costs matter — a single oversized message, and a long conversation whose
whole history is resent every turn. The guard bounds both, and it does it by
*truncating and windowing*, never by rejecting: someone who wrote a lot is often
someone who needs the reply.
"""

from __future__ import annotations

import pytest

from app.services.llm.base import LLMMessage
from app.services.llm.guard import (
    CHARS_PER_TOKEN,
    TRUNCATION_MARKER,
    GuardReport,
    TokenGuard,
)

from .conftest import messages


def turns(count: int, words: int = 6) -> list[LLMMessage]:
    return messages(*[" ".join(["word"] * words) + f" {index}" for index in range(count)])


# --- truncation --------------------------------------------------------------


def test_short_text_is_untouched() -> None:
    guard = TokenGuard(max_input_chars=100)
    assert guard.truncate("hello there") == ("hello there", False)


def test_long_text_is_cut_at_a_word_boundary() -> None:
    guard = TokenGuard(max_input_chars=30)
    text = " ".join(["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"])
    cut, truncated = guard.truncate(text)
    assert truncated is True
    assert cut.endswith(TRUNCATION_MARKER)
    # No word is left half-written: "charli" would be.
    kept = cut[: -len(TRUNCATION_MARKER)]
    assert kept == "alpha bravo charlie delta"
    assert all(word in text for word in kept.split(" "))


def test_truncation_never_exceeds_the_cap() -> None:
    guard = TokenGuard(max_input_chars=40)
    text = "x" * 5000
    cut, truncated = guard.truncate(text)
    assert truncated is True
    assert len(cut) <= 40


def test_a_cap_too_small_for_the_marker_still_fits_the_cap() -> None:
    guard = TokenGuard(max_input_chars=2)
    cut, truncated = guard.truncate("a very long sentence indeed")
    assert truncated is True
    assert len(cut) <= 2


def test_a_redaction_placeholder_is_never_cut_in_half() -> None:
    """``[PHO`` is not a placeholder any more, and would reach the provider as
    half an email address the redactor no longer recognises."""
    guard = TokenGuard(max_input_chars=60)
    text = "email me at [EMAIL] because " + " ".join(["padding"] * 40)
    cut, truncated = guard.truncate(text)
    assert truncated is True
    assert "[EMAIL]" in cut
    # Every bracket that opened also closed: nothing dangles.
    assert cut.count("[") == cut.count("]")
    assert "[" not in cut.replace("[EMAIL]", "").replace(TRUNCATION_MARKER, "")


def test_truncate_rejects_a_zero_cap() -> None:
    with pytest.raises(ValueError):
        TokenGuard().truncate("hello", 0)


# --- token estimation --------------------------------------------------------


def test_token_estimation_is_conservative_and_deterministic() -> None:
    guard = TokenGuard()
    assert guard.estimate_tokens("") == 0
    assert guard.estimate_tokens("a") == 1  # never zero for non-empty text
    assert guard.estimate_tokens("a" * 4) == 1
    assert guard.estimate_tokens("a" * 400) == 100
    assert guard.estimate_tokens("x") == guard.estimate_tokens("x")


# --- conversation window -----------------------------------------------------


def test_a_short_conversation_is_passed_through() -> None:
    guard = TokenGuard(window_turns=10, window_tokens=5000)
    report = guard.guard(turns(4)).report
    assert report.turns_in == 4
    assert report.turns_out == 4
    assert report.dropped_turns == 0
    assert report.within_budget is True


def test_a_long_conversation_is_windowed_to_the_turn_limit() -> None:
    guard = TokenGuard(window_turns=5, window_tokens=100_000)
    guarded = guard.guard(turns(40))
    assert len(guarded.messages) == 5
    # The newest turns are the ones that survive; the oldest are dropped.
    assert guarded.messages[-1].content.endswith("39")
    assert guarded.messages[0].content.endswith("35")
    assert guarded.report.dropped_turns == 35


def test_a_long_conversation_is_windowed_to_the_token_budget() -> None:
    guard = TokenGuard(window_turns=100, window_tokens=200)
    guarded = guard.guard(turns(40))
    assert guarded.report.dropped_turns > 0
    assert guarded.report.input_tokens <= 200 + CHARS_PER_TOKEN
    assert guarded.messages[-1].content.endswith("39")


def test_the_newest_turn_always_survives_however_small_the_budget() -> None:
    """Dropping it would leave the model answering a question it cannot see.
    This is the one rule the budget is not allowed to break."""
    guard = TokenGuard(window_turns=1, window_tokens=1)
    history = [
        LLMMessage(role="user", content="an old question nobody is waiting on"),
        LLMMessage(role="assistant", content="an old answer nobody is waiting on"),
        LLMMessage(role="user", content="the question that matters"),
    ]
    guarded = guard.guard(history)
    assert [message.content for message in guarded.messages] == ["the question that matters"]
    assert guarded.report.turns_out == 1
    assert guarded.report.dropped_turns == 2


def test_the_system_prompt_is_counted_against_the_budget() -> None:
    """A long system prompt leaves less room for the conversation, and the
    guard must know that or the window silently over-spends."""
    guard = TokenGuard(window_turns=50, window_tokens=100)
    without_system = guard.guard(turns(10), None).report.turns_out
    with_system = guard.guard(turns(10), "x" * 200).report.turns_out
    assert without_system == 10
    assert 0 < with_system < without_system


def test_the_report_is_metadata_only() -> None:
    report = GuardReport(input_chars=10, input_tokens=3, turns_in=1, turns_out=1, dropped_turns=0)
    assert report.as_dict()["input_chars"] == 10
    assert "messages" not in report.as_dict()
    assert "content" not in report.as_dict()


def test_messages_are_not_mutated() -> None:
    original = LLMMessage(role="user", content="x" * 500)
    TokenGuard(max_input_chars=40).guard([original])
    assert len(original.content) == 500


def test_guard_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        TokenGuard(max_input_chars=0)
    with pytest.raises(ValueError):
        TokenGuard(window_turns=0)
    with pytest.raises(ValueError):
        TokenGuard(window_tokens=0)
    with pytest.raises(ValueError):
        TokenGuard(chars_per_token=0)


# --- output budget -----------------------------------------------------------


def test_the_output_budget_is_capped_by_the_ceiling() -> None:
    guard = TokenGuard()
    assert guard.output_budget(input_tokens=0, max_tokens=4000) == 4000
    assert guard.output_budget(input_tokens=5000, max_tokens=4000) == 3192
    assert guard.output_budget(input_tokens=99_000, max_tokens=4000) == 1  # never zero


def test_the_output_budget_never_exceeds_what_the_caller_asked_for() -> None:
    guard = TokenGuard()
    assert guard.output_budget(input_tokens=0, max_tokens=10) == 10


def test_describe_is_metadata_only() -> None:
    described = TokenGuard(max_input_chars=123, window_turns=4, window_tokens=99).describe()
    assert described["max_input_chars"] == 123
    assert described["window_turns"] == 4
    assert described["window_tokens"] == 99
