"""The allow-list and its strict schemas (Day 44 tests).

Checks the contract the brief names first: tool schemas.
The allow-list is exactly five tools, every schema is a
closed object (``additionalProperties: false``), and
arguments that do not satisfy a schema are rejected before
anything executes.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.agent.tools import (
    DEFAULT_MAX_STEPS,
    DEFAULT_TOKEN_BUDGET,
    EFFECTING_TOOLS,
    TOOL_REGISTRY,
)

#: The complete surface, per the Day 44 brief. No more,
#: no less: there is deliberately no way to browse, pay,
#: book or send.
EXPECTED_TOOLS = frozenset(
    {
        "search_directory",
        "draft_message",
        "make_appointment_prep",
        "set_reminder",
        "explain_options",
    }
)


def test_the_allow_list_is_exactly_the_five_briefed_tools() -> None:
    assert frozenset(TOOL_REGISTRY) == EXPECTED_TOOLS


def test_only_set_reminder_has_an_external_effect() -> None:
    assert frozenset({"set_reminder"}) == EFFECTING_TOOLS
    for name, spec in TOOL_REGISTRY.items():
        assert spec.requires_confirmation == (name == "set_reminder")


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_every_schema_is_a_strict_closed_object(name: str) -> None:
    spec = TOOL_REGISTRY[name]
    parameters = spec.parameters
    assert parameters["type"] == "object"
    assert parameters["additionalProperties"] is False
    # Every property is typed; nothing is left loose.
    for prop in parameters.get("properties", {}).values():
        assert "type" in prop or "enum" in prop


@pytest.mark.parametrize(
    ("tool", "bad_arguments"),
    [
        (
            "search_directory",
            {"region": "not a region!", "budget": "cheap"},
        ),
        (
            "search_directory",
            {"region": "IN", "surprise": "smuggled"},
        ),
        ("draft_message", {"recipient_type": "principal"}),
        ("draft_message", {"recipient_type": "professor"}),
        (
            "draft_message",
            {
                "recipient_type": "professor",
                "purpose": "x" * 301,
            },
        ),
        (
            "make_appointment_prep",
            {"summary_items": []},
        ),
        (
            "make_appointment_prep",
            {"summary_items": [f"item {n}" for n in range(11)]},
        ),
        (
            "make_appointment_prep",
            {"summary_items": "not a list"},
        ),
        ("set_reminder", {"text": "x"}),
        (
            "set_reminder",
            {"text": "x", "when": "next week"},
        ),
        (
            "set_reminder",
            {"text": "x", "when": 17_000_000_000},
        ),
        ("explain_options", {"topic": "medication"}),
        ("explain_options", {"topic": "therapy_types", "extra": 1}),
    ],
)
def test_invalid_arguments_are_rejected(tool: str, bad_arguments: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        TOOL_REGISTRY[tool].validate_arguments(bad_arguments)


@pytest.mark.parametrize(
    ("tool", "good_arguments"),
    [
        (
            "search_directory",
            {"region": "IN-WB", "language": "hi", "need": "anxiety", "budget": "low_cost"},
        ),
        ("search_directory", {}),
        (
            "draft_message",
            {"recipient_type": "professor", "purpose": "ask for an extension"},
        ),
        (
            "make_appointment_prep",
            {"summary_items": ["one thing"]},
        ),
        (
            "set_reminder",
            {"text": "Call back", "when": "2026-10-17T09:00:00+05:30"},
        ),
        ("explain_options", {"topic": "costs"}),
        ("explain_options", {}),
    ],
)
def test_valid_arguments_pass(tool: str, good_arguments: dict[str, Any]) -> None:
    TOOL_REGISTRY[tool].validate_arguments(good_arguments)


def test_limits_are_positive_and_bounded() -> None:
    assert 1 <= DEFAULT_MAX_STEPS <= 12
    assert 1 <= DEFAULT_TOKEN_BUDGET <= 100_000
