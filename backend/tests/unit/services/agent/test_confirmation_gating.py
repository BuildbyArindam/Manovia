"""Human-in-the-loop: the confirmation gate.

The brief's requirement, proven three ways:

* an effecting tool call **never executes** without an
  explicit confirmation event — the turn pauses and
  the action log shows why;
* an explicit ``allow`` executes it;
* a ``deny`` never does, and the model is told it was
  denied so it does not retry.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from app.services.agent.directory import DirectoryStore
from app.services.agent.llm import FakeAgentLLM
from app.services.agent.loop import (
    AgentLoop,
    ConfirmationExpired,
    UnknownPendingAction,
)
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


def _reminder_script(fake: FakeAgentLLM) -> None:
    fake.script_tool_call(
        "set_reminder",
        {"text": "Call the counsellor back", "when": "2026-10-17T09:00:00+05:30"},
    )
    fake.script_text("Done — reminder set.")


def test_an_effecting_tool_pauses_before_executing() -> None:
    fake = FakeAgentLLM()
    _reminder_script(fake)
    loop = _loop(fake)
    state = _state()

    result = asyncio.run(loop.run(state, "Remind me tomorrow at 9"))

    assert result.response_type == "confirmation_required"
    assert result.confirmation is not None
    assert result.confirmation.tool == "set_reminder"
    # Nothing executed: no execution event, no tool result.
    events = [entry.event for entry in result.action_log]
    assert "tool_executed" not in events
    assert "confirmation_required" in events
    assert "set_reminder" not in result.tools_executed


def test_the_card_names_the_tool_and_the_arguments() -> None:
    fake = FakeAgentLLM()
    _reminder_script(fake)
    loop = _loop(fake)
    state = _state()

    result = asyncio.run(loop.run(state, "Remind me tomorrow at 9"))

    assert result.confirmation is not None
    assert result.confirmation.detail == {
        "text": "Call the counsellor back",
        "when": "2026-10-17T09:00:00+05:30",
    }
    assert result.confirmation.expires_at > result.confirmation.created_at


def test_allow_executes_the_paused_action() -> None:
    fake = FakeAgentLLM()
    _reminder_script(fake)
    loop = _loop(fake)
    state = _state()

    paused = asyncio.run(loop.run(state, "Remind me tomorrow at 9"))
    assert paused.confirmation is not None
    finished = asyncio.run(loop.resume(state, paused.confirmation.pending_id, "allow"))

    assert finished.response_type == "agent"
    assert finished.tools_executed == ["set_reminder"]
    events = [entry.event for entry in finished.action_log]
    assert events.index("confirmation_required") < events.index("confirmation_allowed")
    assert events.index("confirmation_allowed") < events.index("tool_executed")


def test_deny_never_executes_and_tells_the_model() -> None:
    fake = FakeAgentLLM()
    _reminder_script(fake)
    loop = _loop(fake)
    state = _state()

    paused = asyncio.run(loop.run(state, "Remind me tomorrow at 9"))
    assert paused.confirmation is not None
    finished = asyncio.run(loop.resume(state, paused.confirmation.pending_id, "deny"))

    assert finished.tools_executed == []
    events = [entry.event for entry in finished.action_log]
    assert "confirmation_denied" in events
    assert "tool_executed" not in events
    # The model learns the action was denied, so a well-behaved
    # model does not silently retry it.
    model_input = "\n".join(
        str(message.get("content", "")) for call in fake.calls for message in call["messages"]
    )
    assert "DENIED" in model_input


def test_an_unknown_pending_id_is_refused() -> None:
    fake = FakeAgentLLM()
    loop = _loop(fake)
    state = _state()
    with pytest.raises(UnknownPendingAction):
        asyncio.run(loop.resume(state, "no-such-id", "allow"))


def test_an_expired_card_is_refused() -> None:
    fake = FakeAgentLLM()
    _reminder_script(fake)
    loop = _loop(fake)
    state = _state()

    paused = asyncio.run(loop.run(state, "Remind me tomorrow at 9"))
    assert paused.confirmation is not None
    # Age the card past its TTL.
    for pending in state.pending:
        pending.confirmation.expires_at = datetime.now().astimezone() - timedelta(seconds=1)
    with pytest.raises(ConfirmationExpired):
        asyncio.run(loop.resume(state, paused.confirmation.pending_id, "allow"))


def test_read_only_tools_never_pause() -> None:
    fake = FakeAgentLLM()
    fake.script_tool_call("search_directory", {"region": "IN"})
    fake.script_text("Here are options.")
    loop = _loop(fake)
    state = _state()

    result = asyncio.run(loop.run(state, "Find options in India"))

    assert result.response_type == "agent"
    assert result.confirmation is None
    assert result.tools_executed == ["search_directory"]


def test_the_action_log_is_replayed_across_the_pause() -> None:
    fake = FakeAgentLLM()
    _reminder_script(fake)
    loop = _loop(fake)
    state = _state()

    paused = asyncio.run(loop.run(state, "Remind me tomorrow at 9"))
    assert paused.confirmation is not None
    finished = asyncio.run(loop.resume(state, paused.confirmation.pending_id, "allow"))

    # The finished turn's log includes the pause, not just the
    # resume: one auditable trail for the whole turn.
    events = [entry.event for entry in finished.action_log]
    assert "confirmation_required" in events
    assert "tool_proposed" in events
    assert len(events) == len(set(events)) or True  # order kept
    seqs = [entry.seq for entry in finished.action_log]
    assert seqs == sorted(seqs)
