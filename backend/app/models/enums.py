"""Enumerations shared by the ORM models.

Every enum is a :class:`enum.StrEnum` whose *values* are what the database
stores, so the values are stable wire/format strings and renaming a Python
member can never silently change stored data. ``IntEnum`` is used where the
column is numeric (risk levels).
"""

from enum import IntEnum, StrEnum


class ConsentKind(StrEnum):
    """Which agreement a :class:`~app.models.consent.Consent` row records."""

    TERMS = "terms"
    PRIVACY = "privacy"
    AI_DISCLOSURE = "ai_disclosure"
    STORE_CHAT = "store_chat"


class MessageRole(StrEnum):
    """Author of a chat message."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class RiskLevel(IntEnum):
    """Risk tier for a message or a safety event.

    ``0`` is the safe default: a row that has not been triaged carries no risk.
    ``CRISIS`` is the only tier that must be answered with the deterministic
    helpline response before any LLM call (AGENTS.md rule 1).
    """

    NONE = 0
    CAUTION = 1
    ELEVATED = 2
    CRISIS = 3


class SafetyEventSource(StrEnum):
    """Which detector raised a safety event."""

    RULES = "rules"
    ML = "ml"
    LLM_OUTPUT = "llm_output"


class AssessmentInstrument(StrEnum):
    """Self-help screening instruments. Results are never a diagnosis."""

    PHQ9 = "phq9"
    GAD7 = "gad7"


class AssessmentBand(StrEnum):
    """Severity band stored alongside an assessment total."""

    MINIMAL = "minimal"
    MILD = "mild"
    MODERATE = "moderate"
    MODERATELY_SEVERE = "moderately_severe"
    SEVERE = "severe"
