"""Pure scoring for the validated screening instruments.

Nothing here reads the clock, the database, a request, or a model. Given an
instrument and a mapping of item id to answer it returns a total and a band, or
it raises. That is the whole contract, and it is the reason the boundaries can be
tested exhaustively rather than sampled.

Three decisions worth naming, because they are the ways a screener goes wrong
quietly:

* **Missing answers are rejected, never imputed.** Not "treated as zero", not
  "excluded from the total". A PHQ-9 total of 6 from three of nine questions is
  not a score, it is a guess about somebody's fortnight, and both the official
  instructions ("set the value of the scale to missing") and basic honesty point
  the same way. The caller gets :class:`IncompleteAssessmentError` naming the
  missing item ids — ids only, never content, so the error is safe to log.
* **Out-of-range answers are rejected, including ``True``.** ``bool`` is an
  ``int`` in Python, so ``{"phq9_1": True}`` would silently score 1. Booleans,
  floats and numeric strings are all refused: an answer is an option, not a
  number that happens to fit.
* **The item-9 rule is item-level, not total-level.** A total of 4 can still
  contain "thoughts that you would be better off dead" on several days, and an
  average would hide exactly that. Any non-zero answer on an item marked
  ``creates_crisis_response`` is reported separately, so every caller — API,
  UI, tests, any future export — sees the same flag rather than each
  re-deriving it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.models.enums import AssessmentBand, AssessmentInstrument
from app.services.assessments.instruments import (
    Band,
    InstrumentDefinition,
    get_instruments,
)


class AssessmentAnswerError(ValueError):
    """Base class for refused answer sets. Messages carry ids, never content."""


class IncompleteAssessmentError(AssessmentAnswerError):
    """At least one item has no answer. The instrument is not scored."""

    def __init__(self, instrument: AssessmentInstrument, missing: tuple[str, ...]) -> None:
        self.instrument = instrument
        self.missing = missing
        super().__init__(
            f"{instrument.value}: missing answers for {', '.join(missing)}"
            if missing
            else f"{instrument.value}: no answers supplied"
        )


class InvalidAnswerError(AssessmentAnswerError):
    """An answer is not one of the instrument's response option values."""

    def __init__(self, item_id: str, value: object) -> None:
        self.item_id = item_id
        self.value = value
        super().__init__(f"{item_id}: {type(value).__name__} is not an answer between 0 and 3")


class UnknownItemError(AssessmentAnswerError):
    """The answer set names an item the instrument does not have."""

    def __init__(self, instrument: AssessmentInstrument, item_ids: tuple[str, ...]) -> None:
        self.instrument = instrument
        self.item_ids = item_ids
        super().__init__(f"{instrument.value}: unknown items {', '.join(item_ids)}")


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """One scored questionnaire: the total, the band, and the safety flag."""

    instrument: AssessmentInstrument
    #: Answers in item order, so two runs with the same total are still comparable.
    answers: tuple[tuple[str, int], ...]
    total: int
    band: Band
    #: Item ids marked ``creates_crisis_response`` that were answered above zero.
    crisis_items: tuple[str, ...]
    max_total: int

    @property
    def band_id(self) -> AssessmentBand:
        """The stored band value (also the id in the content file)."""
        return self.band.id

    @property
    def flagged_for_crisis(self) -> bool:
        """True when a safety item was answered above zero, whatever the total."""
        return bool(self.crisis_items)

    @property
    def answers_document(self) -> dict[str, int]:
        """The mapping to persist: exactly the item ids, in item order."""
        return dict(self.answers)

    @property
    def band_label(self) -> str:
        """Human label for the band, e.g. ``Moderately severe``."""
        return self.band.label


def _validated_answer(item_id: str, value: object) -> int:
    """Return ``value`` as an answer, or raise :class:`InvalidAnswerError`.

    ``isinstance(value, bool)`` comes first on purpose: in Python a bool *is* an
    int, and a client sending ``true``/``false`` should get a clear rejection
    rather than a score of 1 or 0.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
        raise InvalidAnswerError(item_id, value)
    return value


def score_from_definition(
    definition: InstrumentDefinition, answers: Mapping[str, object]
) -> ScoreResult:
    """Score ``answers`` against an already-loaded instrument.

    Raises :class:`IncompleteAssessmentError` when any item is unanswered,
    :class:`UnknownItemError` when an id is not part of the instrument, and
    :class:`InvalidAnswerError` when a value is not 0-3.
    """
    expected = [item.id for item in definition.items]
    unknown = tuple(sorted(set(answers) - set(expected)))
    if unknown:
        raise UnknownItemError(definition.instrument, unknown)
    missing = tuple(item_id for item_id in expected if item_id not in answers)
    if missing:
        raise IncompleteAssessmentError(definition.instrument, missing)

    ordered = tuple((item_id, _validated_answer(item_id, answers[item_id])) for item_id in expected)
    total = sum(value for _, value in ordered)
    flagged = tuple(
        item.id
        for item in definition.items
        if item.creates_crisis_response and answers[item.id] != 0
    )
    return ScoreResult(
        instrument=definition.instrument,
        answers=ordered,
        total=total,
        band=definition.band_for(total),
        crisis_items=flagged,
        max_total=definition.scoring.total_max,
    )


def score(instrument: AssessmentInstrument | str, answers: Mapping[str, object]) -> ScoreResult:
    """Score one submission against a shipped instrument.

    ``instrument`` may be the enum or its wire value (``"phq9"``); the API
    validates the path segment, and this accepts either so callers do not have
    to remember which layer converts.
    """
    resolved = (
        instrument
        if isinstance(instrument, AssessmentInstrument)
        else AssessmentInstrument(instrument)
    )
    return score_from_definition(get_instruments()[resolved], answers)


def band_for(instrument: AssessmentInstrument | str, total: int) -> Band:
    """The band for a bare total, without an answer set.

    Used by the history view and by tests; the same table the scoring path uses,
    so a band can never be computed two ways.
    """
    resolved = (
        instrument
        if isinstance(instrument, AssessmentInstrument)
        else AssessmentInstrument(instrument)
    )
    return get_instruments()[resolved].band_for(total)


def bands(instrument: AssessmentInstrument | str) -> tuple[Band, ...]:
    """Every band, in ascending order — for the result page and the print view."""
    resolved = (
        instrument
        if isinstance(instrument, AssessmentInstrument)
        else AssessmentInstrument(instrument)
    )
    return tuple(sorted(get_instruments()[resolved].scoring.bands, key=lambda band: band.min))


def item_ids(instrument: AssessmentInstrument | str) -> tuple[str, ...]:
    """Every item id, in order — the keys a valid submission must have."""
    resolved = (
        instrument
        if isinstance(instrument, AssessmentInstrument)
        else AssessmentInstrument(instrument)
    )
    return tuple(item.id for item in get_instruments()[resolved].items)
