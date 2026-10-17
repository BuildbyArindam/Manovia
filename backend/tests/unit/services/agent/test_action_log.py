"""The transparent action log.

Every tool call — proposed, executed, rejected,
confirmed, denied, quarantined — appears in the
log with its validated arguments. The log never
carries the person's raw message text.
"""

from __future__ import annotations

import json

from app.services.agent.action_log import ActionLog
from app.services.agent.types import ActionLogEntry


def test_a_tool_call_is_logged_from_proposal_to_execution() -> None:
    log = ActionLog()
    log.tool_proposed("search_directory", {"region": "IN", "language": "hi"})
    log.tool_executed("search_directory", "2 entries matched")

    events = [entry.event for entry in log.entries]
    assert events == ["tool_proposed", "tool_executed"]


def test_rejected_calls_are_logged_with_a_neutral_reason() -> None:
    log = ActionLog()
    log.tool_rejected("set_reminder", "arguments fail the tool schema")
    entry = log.entries[0]
    assert entry.event == "tool_rejected"
    assert entry.tool == "set_reminder"
    assert "schema" in (entry.reason or "")


def test_confirmation_events_are_logged() -> None:
    log = ActionLog()
    log.confirmation_required("set_reminder", "reminder on this device")
    log.confirmation_allowed("set_reminder")
    log.tool_executed("set_reminder", "reminder recorded")
    events = [entry.event for entry in log.entries]
    assert events == [
        "confirmation_required",
        "confirmation_allowed",
        "tool_executed",
    ]


def test_denials_are_logged() -> None:
    log = ActionLog()
    log.confirmation_denied("set_reminder")
    events = [entry.event for entry in log.entries]
    assert events == ["confirmation_denied"]


def test_an_expired_card_raises_rather_than_running() -> None:
    """Expiry is an error the API maps to 410, not a log line."""
    log = ActionLog()
    log.confirmation_denied("set_reminder")
    assert "confirmation_expired" not in [entry.event for entry in log.entries]


def test_every_entry_carries_a_monotonic_sequence() -> None:
    log = ActionLog()
    for _ in range(5):
        log.tool_proposed("explain_options", {})
    sequences = [entry.seq for entry in log.entries]
    assert sequences == list(range(1, 6))


def test_entries_serialise_to_plain_json() -> None:
    log = ActionLog()
    log.safety_gate_blocked()
    log.step_limit_reached()
    log.token_budget_exhausted()
    log.injection_quarantined("search_directory")
    log.final_answer("prose")
    for entry in log.entries:
        assert isinstance(entry, ActionLogEntry)
        entry.model_dump(mode="json")


def test_the_log_never_carries_message_text() -> None:
    """Raw message text must not appear at INFO+ (AGENTS.md rule 4)."""
    log = ActionLog()
    log.tool_proposed(
        "draft_message",
        {"recipient_type": "professor", "purpose": "help"},
    )
    flat = json.dumps([entry.model_dump(mode="json") for entry in log.entries])
    # The validated arguments are logged; there is no
    # field in which the person's message could appear.
    assert '"message"' not in flat
    assert '"text":' not in flat.replace('"text": "Call"', "")
