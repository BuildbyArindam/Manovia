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


class SafetyEventCategory(StrEnum):
    """Closed, text-free vocabulary for why a safety event was recorded."""

    DIAGNOSIS = "diagnosis"
    MEDICATION = "medication"
    SELF_HARM_METHOD = "self_harm_method"
    IDENTITY_CLAIM = "identity_claim"
    OUTCOME_PROMISE = "outcome_promise"
    ISOLATION_DEPENDENCE = "isolation_dependence"
    TOO_LONG = "too_long"
    WRONG_LANGUAGE = "wrong_language"
    MISSING_CHECK_IN = "missing_check_in"
    #: Day 29 (red team): the reply repeats or describes the system prompt.
    PROMPT_LEAK = "prompt_leak"
    #: Day 29 (red team): the reply plays along with a jailbreak ("DAN mode on").
    JAILBREAK = "jailbreak"


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


class PlanItemKind(StrEnum):
    """What a daily-plan suggestion asks for.

    The vocabulary is closed: the planner can only emit kinds the UI knows how
    to render, and a stored row can only hold one of these values.
    """

    CHECK_IN = "check_in"
    EXERCISE = "exercise"
    THOUGHT_RECORD = "thought_record"
    REST = "rest"
    #: Support options shown after a positive PHQ-9 item-9 answer. This is the
    #: *only* way an assessment answer may influence the plan.
    SUPPORT = "support"


class PlanItemStatus(StrEnum):
    """Lifecycle of one suggestion: proposed, then done or skipped."""

    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"
