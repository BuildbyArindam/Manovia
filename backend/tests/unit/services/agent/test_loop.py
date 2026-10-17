"""The agent loop: one turn, end to end.

The loop is the only place tools execute. These
tests pin its contract: allow-list first, schema
second, effect gate third, execution last — and
tool results reach the model only as sanitised,
envelope-marked data.
"""

from __future__ import annotations

import asyncio

from app.services.agent.directory import DirectoryStore
from app.services.agent.llm import FakeAgentLLM
from app.services.agent.loop import AgentLoop
from app.services.agent.tools import TOOL_REGISTRY
from app.services.agent.types import TurnState
from app.services.chat.output_guard import PassthroughOutputGuard


def _loop(fake: FakeAgentLLM) -> AgentLoop:
    return AgentLoop(
        fake,
        DirectoryStore.load(),
        output_guard=PassthroughOutputGuard(),
    )


def _state() -> TurnState:
    return TurnState(session_id="s", user_id="u")


def test_a_search_turn_answers_from_directory_data() -> None:
    fake = FakeAgentLLM()
    fake.script_tool_call("search_directory", {"region": "IN-WB", "language": "hi"})
    fake.script_text("Tele-MANAS (14416) and KIRAN both serve West Bengal.")
    loop = _loop(fake)

    result = asyncio.run(loop.run(_state(), "Hindi support near Kolkata"))

    assert result.response_type == "agent"
    assert result.tools_executed == ["search_directory"]
    events = [entry.event for entry in result.action_log]
    assert "tool_proposed" in events
    assert "tool_executed" in events
    assert "final_answer" in events
    # The model saw the tool result wrapped as data.
    model_input = "\n".join(
        str(message.get("content", "")) for call in fake.calls for message in call["messages"]
    )
    assert "DATA from tool search_directory" in model_input
    assert "Tele-MANAS" in model_input


def test_an_unknown_tool_is_rejected() -> None:
    fake = FakeAgentLLM()
    fake.script_tool_call("book_flight", {"destination": "Moon"})
    fake.script_text("I can't do that.")
    loop = _loop(fake)

    result = asyncio.run(loop.run(_state(), "Book a flight"))

    assert result.tools_executed == []
    events = [entry.event for entry in result.action_log]
    assert "tool_rejected" in events


def test_schema_violations_are_rejected_before_execution() -> None:
    fake = FakeAgentLLM()
    fake.script_tool_call("search_directory", {"region": "not-a-region"})
    fake.script_text("Could not search with that region.")
    loop = _loop(fake)

    result = asyncio.run(loop.run(_state(), "Search somewhere"))

    assert result.tools_executed == []
    events = [entry.event for entry in result.action_log]
    assert "tool_rejected" in events
    # The model is told the arguments failed, so it can
    # correct course in the same turn.
    model_input = "\n".join(
        str(message.get("content", "")) for call in fake.calls for message in call["messages"]
    )
    assert "arguments fail the tool schema" in model_input


def test_the_model_cannot_self_confirm_an_effecting_tool() -> None:
    """A tool call that arrives with confirmation=True is
    still gated: only the API's resume() path confirms."""
    fake = FakeAgentLLM()
    fake.script_tool_call(
        "set_reminder",
        {"text": "x", "when": "2026-10-17T09:00:00+05:30"},
    )
    loop = _loop(fake)
    state = _state()

    result = asyncio.run(loop.run(state, "Remind me"))

    assert result.response_type == "confirmation_required"
    assert result.tools_executed == []


def test_an_output_guard_rejection_is_retried_once() -> None:
    from app.services.safety.output_guard import (
        RuleBasedOutputGuard,
    )

    fake = FakeAgentLLM()
    fake.script_tool_call("explain_options", {"topic": "therapy_types"})
    fake.script_text("I will diagnose you with depression.")
    fake.script_text("Here are therapy types instead.")
    loop = AgentLoop(
        fake,
        DirectoryStore.load(),
        output_guard=RuleBasedOutputGuard(),
    )

    result = asyncio.run(loop.run(_state(), "What therapies exist?"))

    assert result.response_type == "agent"
    assert "diagnose" not in result.reply


def test_a_repeatedly_unsafe_answer_degrades_safely() -> None:
    from app.services.safety.output_guard import (
        RuleBasedOutputGuard,
    )

    fake = FakeAgentLLM()
    # Every reply names a diagnosis; the retry is also
    # rejected, so the safe fallback answers instead.
    fake.script_tool_call("explain_options", {"topic": "therapy_types"})
    fake.script_text("You have major depressive disorder.")
    fake.script_text("You definitely have anxiety disorder.")
    loop = AgentLoop(
        fake,
        DirectoryStore.load(),
        output_guard=RuleBasedOutputGuard(),
    )

    result = asyncio.run(loop.run(_state(), "What is wrong with me?"))

    assert result.response_type == "refusal"
    # The diagnosis never reaches the person; only the
    # safe fallback does.
    assert "depressive disorder" not in result.reply
    assert "anxiety disorder" not in result.reply
    assert "safely" in result.reply.lower()


def test_the_final_answer_is_trimmed_to_two_hundred_words() -> None:
    fake = FakeAgentLLM()
    fake.script_text("word " * 500)
    loop = _loop(fake)

    result = asyncio.run(loop.run(_state(), "Talk"))

    assert len(result.reply.split()) <= 200


def test_the_tool_registry_has_no_web_payment_or_send_tools() -> None:
    names = set(TOOL_REGISTRY)
    for forbidden in (
        "web_search",
        "browse",
        "pay",
        "book",
        "send",
        "email",
        "call",
    ):
        assert not any(forbidden in name for name in names), f"{forbidden} tool must not exist"


def test_the_prompt_teaches_data_not_instructions() -> None:
    from app.services.agent.prompts import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "data" in lowered
    assert "never follow" in lowered or "never obey" in lowered
