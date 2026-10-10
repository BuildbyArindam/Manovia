"""Shared vocabulary for the safety service: levels, categories, assessments.

This module is deliberately small so that :mod:`app.services.safety.patterns`,
:mod:`app.services.safety.normalise`, :mod:`app.services.safety.rules` and
:mod:`app.services.safety.escalation` can all import it without cycles. It
imports the ORM enums only to *map onto* them.

The privacy rule that shapes every type here (AGENTS.md safety rule 5, and the
``safety_events`` table's "metadata only" design): **an assessment describes a
detection, never the text that produced it.** Levels, categories and stable
pattern ids travel; words do not. :class:`RiskAssessment` enforces that with a
validator, and ``tests/safety/test_no_raw_text.py`` enforces it across the whole
package.
"""

from __future__ import annotations

import hashlib
import re
from enum import IntEnum, StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import RiskLevel as StoredRiskLevel

#: Pattern ids are ``<group>.<snake_case>`` — enforced so a rationale code can
#: never carry a fragment of somebody's message.
_RATIONALE_CODE_RE: Final = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")


class RiskLevel(IntEnum):
    """How seriously the engine reads one message.

    Five tiers, ordered so ``>=`` reads like a sentence
    (``if assessment.level >= RiskLevel.HIGH``).

    The database columns (``messages.risk_level``, ``safety_events.risk_level``)
    are four-tier ``SmallInteger`` values from Day 3, so :meth:`to_stored`
    collapses HIGH and IMMINENT onto the stored CRISIS tier. Widening that column
    is a migration decision (ADR 0008 §4), not something a detector may do
    silently.
    """

    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    IMMINENT = 4

    @property
    def label(self) -> str:
        """The wire/API form: ``none``, ``low``, ``medium``, ``high``, ``imminent``."""
        return self.name.lower()

    @property
    def is_crisis(self) -> bool:
        """True for the two tiers answered with the deterministic reply."""
        return self >= RiskLevel.HIGH

    def to_stored(self) -> StoredRiskLevel:
        """Map onto the four-tier database column (ADR 0008 §4)."""
        return _STORED_BY_LEVEL[self]

    @classmethod
    def parse(cls, value: str | int | RiskLevel) -> RiskLevel:
        """Build a level from a wire string, an int, or another level."""
        if isinstance(value, RiskLevel):
            return value
        if isinstance(value, int):
            return cls(value)
        return cls[value.strip().upper()]


_STORED_BY_LEVEL: Final[dict[RiskLevel, StoredRiskLevel]] = {
    RiskLevel.NONE: StoredRiskLevel.NONE,
    RiskLevel.LOW: StoredRiskLevel.CAUTION,
    RiskLevel.MEDIUM: StoredRiskLevel.ELEVATED,
    RiskLevel.HIGH: StoredRiskLevel.CRISIS,
    RiskLevel.IMMINENT: StoredRiskLevel.CRISIS,
}

#: Levels in ascending order — used by reports and the case-count table.
LEVEL_ORDER: Final[tuple[RiskLevel, ...]] = tuple(RiskLevel)


class RiskCategory(StrEnum):
    """The eight things the engine looks for.

    ``ACCESS_TO_MEANS`` is a *risk factor*, not advice: it records that a message
    mentioned having something available, which changes how urgent the reply must
    be. Nothing downstream may turn a category into a description of a method
    (WHO safe-messaging guidance; see ``docs/safety-design.md`` §7).
    """

    SUICIDAL_IDEATION = "suicidal_ideation"
    SELF_HARM = "self_harm"
    INTENT_PLAN = "intent_plan"
    ACCESS_TO_MEANS = "access_to_means"
    HARM_TO_OTHERS = "harm_to_others"
    ABUSE_DISCLOSURE = "abuse_disclosure"
    SEVERE_HOPELESSNESS = "severe_hopelessness"
    ACUTE_MEDICAL = "acute_medical"


class AssessmentContext(BaseModel):
    """Pragmatic facts about the message — never its content.

    These are the flags the escalation policy reads *alongside* the level: who
    the sentence is about, whether the speaker is in it, whether a time is
    attached, and how much the engine threw away before deciding. A reviewer can
    reconstruct "why" from this plus ``rationale_codes`` without seeing the words.
    """

    model_config = ConfigDict(frozen=True)

    #: The speaker appears in the message ("i", "main", "ami", "myself").
    first_person: bool = False
    #: The message is about somebody else, or is quoted from fiction or a report.
    third_person: bool = False
    #: The risky wording appeared only inside quotation marks.
    quoted: bool = False
    #: A fiction or report frame is present ("in a movie", "for my novel",
    #: "hypothetically"). Escalation reads this to pick the supporter-facing
    #: template: a writer asking about a character must not be answered as if
    #: they were describing themselves.
    fiction_frame: bool = False
    #: A time marker is present ("tonight", "aaj raat") — what separates a plan
    #: from an imminent one.
    timeframe: bool = False
    #: Hits dropped because a negation cancelled them ("I *don't* want to die").
    negated_hits: int = 0
    #: Hits dropped because the context was plainly figurative ("this traffic is
    #: killing me").
    figurative_hits: int = 0
    #: Language hint from the caller, if any. The engine does not route on it:
    #: every pattern runs against every message.
    language: str | None = None
    #: True when the input was cut at the engine's character limit.
    truncated: bool = False
    #: Normalisation variants actually searched (a count, never their text).
    variants_searched: int = 0


class RiskAssessment(BaseModel):
    """What the rules engine decided about one message.

    ``matched_categories`` and ``rationale_codes`` are stable vocabularies, not
    evidence: a code names the *pattern* that fired (``si.want_to_die``), never
    the words it matched. That is what makes this object safe to log, cache,
    return over an API, and store as a ``safety_events`` row.
    """

    model_config = ConfigDict(frozen=True)

    level: RiskLevel
    matched_categories: tuple[RiskCategory, ...] = ()
    rationale_codes: tuple[str, ...] = ()
    context: AssessmentContext = Field(default_factory=AssessmentContext)

    @property
    def is_crisis(self) -> bool:
        """True when the reply must be the deterministic pre-written message."""
        return self.level.is_crisis

    def to_stored_level(self) -> StoredRiskLevel:
        """The four-tier value a ``safety_events`` row stores."""
        return self.level.to_stored()

    @model_validator(mode="after")
    def _no_raw_text(self) -> RiskAssessment:
        """Refuse to exist if it ever carries something that looks like prose.

        A guard rail against a future well-meaning ``matched_text`` field: if a
        rationale code contains a space, a capital letter or punctuation, it is
        somebody's words and this object must not be built.
        """
        for code in self.rationale_codes:
            if not _RATIONALE_CODE_RE.match(code):
                raise ValueError(f"rationale code {code!r} does not look like a pattern id")
        return self


def empty_assessment(*, rationale: str | None = None) -> RiskAssessment:
    """The assessment for text that carries no signal at all."""
    return RiskAssessment(
        level=RiskLevel.NONE,
        rationale_codes=(rationale,) if rationale else (),
    )


def text_fingerprint(text: str) -> str:
    """A 16-hex SHA-256 prefix, for logs.

    The convention the Day 6 dev endpoint already uses: enough to correlate two
    log lines about the same message, useless for reading it.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
