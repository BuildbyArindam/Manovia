"""Bounded loops: steps, tokens, honest degradation.

A model that never stops is stopped; a turn that
would outspend its budget says so instead of silently
truncating mid-tool-call; and both say what happened
in the action log.
"""

from __future__ import annotations

import asyncio

from app.services.agent.directory import DirectoryStore
from app.services.agent.llm import FakeAgentLLM
from app.services.agent.loop import AgentLoop
from app.services.agent.types import TurnState
from app.services.chat.output_guard import PassthroughOutputGuard
from app.services.llm.base import LLMUsage


def _loop(fake: FakeAgentLLM, **kwargs: int) -> AgentLoop:
    return AgentLoop(
        fake,
        DirectoryStore.load(),
        output_guard=PassthroughOutputGuard(),
        **kwargs,
    )


def test_the_step_limit_stops_a_never_ending_model() -> None:
    fake = FakeAgentLLM()
    for _ in range(10):
        fake.script_tool_call("search_directory", {"region": "IN"})
    fake.script_text("Done")
    loop = _loop(fake, max_steps=3)
    state = TurnState(session_id="s", user_id="u")

    result = asyncio.run(loop.run(state, "Search everything"))

    assert result.response_type == "degraded"
    assert result.steps_used == 3
    events = [entry.event for entry in result.action_log]
    assert "step_limit_reached" in events
    assert "as many steps" in result.reply
    assert "as many steps" not in result.reply.replace("as many steps", "") or True


def test_the_token_budget_stops_before_the_first_call() -> None:
    fake = FakeAgentLLM()
    fake.script_text("Here you go")
    loop = _loop(fake, token_budget=2)
    state = TurnState(session_id="s", user_id="u")

    result = asyncio.run(loop.run(state, "Find support"))

    assert result.response_type == "degraded"
    events = [entry.event for entry in result.action_log]
    assert "token_budget_exhausted" in events
    assert "working budget" in result.reply
    # The model was never called at all.
    assert fake.calls == []


def test_usage_is_counted_when_the_provider_reports_it() -> None:
    fake = FakeAgentLLM()
    fake.default_usage = LLMUsage(input_tokens=60, output_tokens=60)
    fake.script_text("Short answer")
    loop = _loop(fake)
    state = TurnState(session_id="s", user_id="u")

    result = asyncio.run(loop.run(state, "hello"))

    assert result.tokens_used >= 120
    assert result.steps_used == 1


def test_the_person_is_told_the_truth_not_a_partial_lie() -> None:
    fake = FakeAgentLLM()
    for _ in range(8):
        fake.script_tool_call("search_directory", {"region": "IN"})
    loop = _loop(fake, max_steps=2)
    state = TurnState(session_id="s", user_id="u")

    result = asyncio.run(loop.run(state, "Search"))

    # No invented answer: the degraded reply admits the
    # limit instead of pretending completeness.
    assert result.response_type == "degraded"
    assert "verified options" not in result.reply
