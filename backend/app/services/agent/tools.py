"""The allow-listed toolset: the complete surface of the agent.

Five tools, nothing else. There is deliberately **no** tool to
browse the web, make a payment, book anything, or send a
message on the person's behalf — the Day 44 brief forbids all
four, and an allow-list that does not contain them is the only
enforceable form of that prohibition. If a model asks for
``web_search`` or ``send_email``, the loop rejects the call
and logs it: the allow-list is checked at execution time, not
by trusting the model to stay inside it.

Every tool's arguments are a **strict JSON schema**:
``additionalProperties: false``, closed enums wherever the
vocabulary is closed, bounded strings everywhere else. The
schema is the contract — a call that does not satisfy it is
rejected before anything runs.

``requires_confirmation`` marks tools with an external effect.
``set_reminder`` writes a notification the person will see
later, so it is the one effecting tool in the set; the other
four are read-only or produce text the person edits and sends
themselves.
"""

from __future__ import annotations

from typing import Any, Final

from jsonschema import Draft202012Validator
from pydantic import BaseModel

#: Hard ceiling on steps per turn. A loop that needs more than
#: this is misbehaving, not helping.
DEFAULT_MAX_STEPS: Final = 6

#: Cumulative token ceiling per turn (input + output).
DEFAULT_TOKEN_BUDGET: Final = 4_000

#: Closed vocabularies. Kept here so the schema, the executor
#: and the UI all read one list.
RECIPIENT_TYPES: Final = (
    "professor",
    "manager",
    "counsellor",
    "doctor",
    "friend",
    "family",
    "landlord",
    "other",
)
EXPLAIN_TOPICS: Final = (
    "therapy_types",
    "helpline_vs_therapy",
    "first_appointment",
    "costs",
    "crisis_plan",
)
NEEDS: Final = (
    "emotional_support",
    "crisis",
    "anxiety",
    "depression",
    "stress",
    "exam_stress",
    "relationships",
    "substance_use",
    "mood_disorders",
    "grief",
    "loneliness",
    "students",
)
BUDGETS: Final = ("free", "low_cost", "any")


def _string_schema(description: str, max_length: int) -> dict[str, Any]:
    return {"type": "string", "description": description, "maxLength": max_length}


class ToolSpec(BaseModel):
    """One allow-listed tool."""

    name: str
    description: str
    parameters: dict[str, Any]
    requires_confirmation: bool = False
    effect: str = "none"

    @property
    def schema_validator(self) -> Draft202012Validator:
        return Draft202012Validator(self.parameters)

    def validate_arguments(self, arguments: dict[str, Any]) -> None:
        """Raise ``ValueError`` listing every schema violation."""
        errors = sorted(self.schema_validator.iter_errors(arguments), key=str)
        if errors:
            first = errors[0]
            location = "/".join(str(part) for part in first.absolute_path) or "<root>"
            raise ValueError(f"arguments fail the schema at {location}: {first.message}")


SEARCH_DIRECTORY_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "region": {
            "type": "string",
            "description": "ISO-ish region code, e.g. IN, IN-WB, US, GB, or GLOBAL.",
            "pattern": "^[A-Z]{2}(-[A-Z0-9]{2,})?$",
            "maxLength": 12,
        },
        "language": {
            "type": "string",
            "description": "BCP-47-ish language code, lowercase, e.g. en, hi, bn.",
            "pattern": "^[a-z]{2,3}$",
            "maxLength": 3,
        },
        "need": {
            "type": "string",
            "enum": list(NEEDS),
            "description": "What kind of support is being looked for.",
        },
        "budget": {
            "type": "string",
            "enum": list(BUDGETS),
            "description": "free admits only no-cost options; low_cost admits free and low-cost.",
        },
    },
    "additionalProperties": False,
}

DRAFT_MESSAGE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "recipient_type": {
            "type": "string",
            "enum": list(RECIPIENT_TYPES),
            "description": "Who the message is for.",
        },
        "purpose": _string_schema("What the message should say, in the person's own words.", 300),
    },
    "required": ["recipient_type", "purpose"],
    "additionalProperties": False,
}

APPOINTMENT_PREP_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "summary_items": {
            "type": "array",
            "description": "What the person wants to mention to the counsellor or doctor.",
            "items": _string_schema("One thing to raise in the appointment.", 500),
            "minItems": 1,
            "maxItems": 10,
        },
    },
    "required": ["summary_items"],
    "additionalProperties": False,
}

SET_REMINDER_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "text": _string_schema("The reminder, in the person's own words.", 200),
        "when": {
            "type": "string",
            "format": "date-time",
            # ISO 8601 with an explicit offset, e.g.
            # 2026-10-17T09:00:00+05:30.
            "pattern": (
                "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})$"
            ),
            "description": "When to remind, as an ISO 8601 date-time with offset.",
        },
    },
    "required": ["text", "when"],
    "additionalProperties": False,
}

EXPLAIN_OPTIONS_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "topic": {
            "type": "string",
            "enum": list(EXPLAIN_TOPICS),
            "description": "Which set of options to explain.",
        },
    },
    "additionalProperties": False,
}

#: The allow-list itself. A tool not in this dict does not exist,
#: whatever the model or a poisoned tool result claims.
TOOL_REGISTRY: Final[dict[str, ToolSpec]] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            name="search_directory",
            description=(
                "Search the curated directory of verified support options "
                "(campus counselling, national helplines, free/low-cost therapy "
                "directories, support groups) by region, language, need and budget. "
                "Returns matching entries with source, languages, cost and hours."
            ),
            parameters=SEARCH_DIRECTORY_SCHEMA,
        ),
        ToolSpec(
            name="draft_message",
            description=(
                "Draft a message (email to a professor, note to a manager, and so on). "
                "Returns TEXT THE PERSON EDITS AND SENDS THEMSELVES — the agent never "
                "sends anything."
            ),
            parameters=DRAFT_MESSAGE_SCHEMA,
        ),
        ToolSpec(
            name="make_appointment_prep",
            description=(
                "Build a prep sheet for a first appointment with a counsellor or "
                "doctor: what to say, what to ask, what to bring."
            ),
            parameters=APPOINTMENT_PREP_SCHEMA,
        ),
        ToolSpec(
            name="set_reminder",
            description=(
                "Schedule a LOCAL notification reminder on this device, e.g. "
                "'take the leaflet to the appointment'. Requires the person's "
                "explicit confirmation before it is created."
            ),
            parameters=SET_REMINDER_SCHEMA,
            requires_confirmation=True,
            effect="local_notification",
        ),
        ToolSpec(
            name="explain_options",
            description=(
                "Explain, in plain language, the person's options for a topic "
                "(therapy types, helpline vs therapy, first appointment, costs, "
                "crisis plan)."
            ),
            parameters=EXPLAIN_OPTIONS_SCHEMA,
        ),
    )
}

#: Tools whose execution changes something outside this turn.
EFFECTING_TOOLS: Final[frozenset[str]] = frozenset(
    name for name, spec in TOOL_REGISTRY.items() if spec.requires_confirmation
)

#: Every tool name, for the model-facing tool list.
TOOL_NAMES: Final[frozenset[str]] = frozenset(TOOL_REGISTRY)
