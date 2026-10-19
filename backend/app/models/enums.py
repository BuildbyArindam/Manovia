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


class ShareFormat(StrEnum):
    """The export formats a clinician share can carry.

    ``pdf`` is the printable human document; ``fhir`` is the HL7 FHIR R4 Bundle
    (see :mod:`app.services.sharing.fhir`). A share always carries at least one.
    """

    PDF = "pdf"
    FHIR = "fhir"


class ShareItem(StrEnum):
    """What a person may choose to include in a share, one item at a time.

    The set is closed and stored by value, so a row written today still means
    the same thing after a rename. Everything *not* in this list is never shared
    — chat messages, journal entries and mood check-in notes cannot be selected
    because there is no member for them.
    """

    #: Mood check-in summary over the chosen window (numbers only, never notes).
    MOOD_TREND = "mood_trend"
    #: PHQ-9 / GAD-7 totals, bands and dates (never the per-item answers).
    ASSESSMENTS = "assessments"
    #: Thought records the person ticks, one by one.
    THOUGHT_RECORDS = "thought_records"
    #: Which exercises they used and how much each helped (ratings only).
    EXERCISE_HELPFULNESS = "exercise_helpfulness"
    #: A free-text note they write for the appointment.
    NOTE = "note"
    #: Questions they want to ask.
    QUESTIONS = "questions"
    #: Their account email, so the clinician knows whose summary it is. Off by
    #: default: an anonymous summary is the privacy-first choice.
    IDENTITY = "identity"


#: The order items are listed in, in the builder, the preview and the document.
SHARE_ITEM_ORDER: tuple[ShareItem, ...] = (
    ShareItem.MOOD_TREND,
    ShareItem.ASSESSMENTS,
    ShareItem.THOUGHT_RECORDS,
    ShareItem.EXERCISE_HELPFULNESS,
    ShareItem.NOTE,
    ShareItem.QUESTIONS,
    ShareItem.IDENTITY,
)


class ShareEventKind(StrEnum):
    """Closed vocabulary for the share audit log the owner can read.

    No member carries free text and none is derived from a viewer's input, so
    the log can be shown to the person who owns the share without becoming a
    channel for somebody else's words.
    """

    CREATED = "created"
    #: A viewer passed the passcode and was handed the ciphertext.
    OPENED = "opened"
    PASSCODE_FAILED = "passcode_failed"
    #: The passcode was wrong often enough that the link is locked for a while.
    LOCKED = "locked"
    REVOKED = "revoked"
    #: Somebody presented a link whose window had closed.
    EXPIRED = "expired"
    VIEW_LIMIT_REACHED = "view_limit_reached"
    #: Somebody presented a link the owner had already revoked. The public
    #: answer is the same unhelpful 404 as for an unknown token; this line is
    #: for the owner, who has a right to know an old link is still circulating.
    ACCESS_AFTER_REVOKE = "access_after_revoke"


class ShareCipher(StrEnum):
    """The content-encryption label a share row carries.

    One member today (AES-256-GCM with a 96-bit nonce, derived through HKDF —
    see :mod:`app.services.sharing.crypto`). Storing the label means a future
    algorithm is a visible migration with a read path for both, never a guess.
    """

    A256GCM = "A256GCM"
