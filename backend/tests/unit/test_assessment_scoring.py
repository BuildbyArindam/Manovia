"""Scoring is a pure function, so it is tested exhaustively rather than sampled.

The table below walks every band edge on both instruments — the score just below
a cutpoint, the cutpoint itself, the top of the band, and the first score of the
next band — plus the two extremes and out-of-range totals. If a band boundary
ever moves, one of these rows turns red with the exact number in its name.

The item-9 cases are here rather than only in the API tests because this is the
layer that decides: a total of 1 with "thoughts that you would be better off
dead" on several days must be flagged, and a total of 19 with item 9 answered
"Not at all" must not be. An average cannot make that call, so the scorer does.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.models.enums import AssessmentBand, AssessmentInstrument
from app.services.assessments import (
    IncompleteAssessmentError,
    InvalidAnswerError,
    UnknownItemError,
    bands,
    item_ids,
    score,
)

PHQ9 = AssessmentInstrument.PHQ9
GAD7 = AssessmentInstrument.GAD7

#: (instrument, total, expected band) — every edge, both ends of every band.
BAND_EDGES: tuple[tuple[AssessmentInstrument, int, AssessmentBand], ...] = (
    # PHQ-9: 0-4 minimal, 5-9 mild, 10-14 moderate, 15-19 moderately severe, 20-27 severe
    (PHQ9, 0, AssessmentBand.MINIMAL),
    (PHQ9, 4, AssessmentBand.MINIMAL),
    (PHQ9, 5, AssessmentBand.MILD),
    (PHQ9, 9, AssessmentBand.MILD),
    (PHQ9, 10, AssessmentBand.MODERATE),
    (PHQ9, 14, AssessmentBand.MODERATE),
    (PHQ9, 15, AssessmentBand.MODERATELY_SEVERE),
    (PHQ9, 19, AssessmentBand.MODERATELY_SEVERE),
    (PHQ9, 20, AssessmentBand.SEVERE),
    (PHQ9, 27, AssessmentBand.SEVERE),
    # GAD-7: 0-4 minimal, 5-9 mild, 10-14 moderate, 15-21 severe
    (GAD7, 0, AssessmentBand.MINIMAL),
    (GAD7, 4, AssessmentBand.MINIMAL),
    (GAD7, 5, AssessmentBand.MILD),
    (GAD7, 9, AssessmentBand.MILD),
    (GAD7, 10, AssessmentBand.MODERATE),
    (GAD7, 14, AssessmentBand.MODERATE),
    (GAD7, 15, AssessmentBand.SEVERE),
    (GAD7, 21, AssessmentBand.SEVERE),
)


def all_answers(instrument: AssessmentInstrument, value: int, **overrides: int) -> dict[str, int]:
    """Every item answered ``value``, with named items overridden."""
    answers = dict.fromkeys(item_ids(instrument), value)
    answers.update(overrides)
    return answers


# --------------------------------------------------------------------------- #
# Band boundaries
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("instrument", "total", "expected"), BAND_EDGES)
def test_band_for_total_matches_the_official_cutpoints(
    instrument: AssessmentInstrument, total: int, expected: AssessmentBand
) -> None:
    answers = all_answers(instrument, 0)
    # Build an answer set that sums to `total`. Item 9 is included where the
    # arithmetic needs it: this table is about bands, and the item-9 flag has its
    # own tests below.
    remaining = total
    for item_id in item_ids(instrument):
        step = min(3, remaining)
        answers[item_id] = step
        remaining -= step
    assert remaining == 0, "the test table asks for a total no answer set can reach"
    assert sum(answers.values()) == total
    result = score(instrument, answers)
    assert result.total == total
    assert result.band.id is expected


@pytest.mark.parametrize("instrument", [PHQ9, GAD7])
@pytest.mark.parametrize("total", [-1, 28])
def test_totals_outside_the_instrument_range_are_refused(
    instrument: AssessmentInstrument, total: int
) -> None:
    with pytest.raises(ValueError, match="outside"):
        _band_for_total(instrument, total)


def _band_for_total(instrument: AssessmentInstrument, total: int) -> AssessmentBand:
    """Ask the band table directly, the way the history view does."""
    from app.services.assessments import band_for

    return band_for(instrument, total).id


# --------------------------------------------------------------------------- #
# Extremes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("instrument", "total_max"),
    [(PHQ9, 27), (GAD7, 21)],
)
def test_all_zeros_is_minimal(instrument: AssessmentInstrument, total_max: int) -> None:
    result = score(instrument, all_answers(instrument, 0))
    assert result.total == 0
    assert result.band.id is AssessmentBand.MINIMAL
    assert result.max_total == total_max
    assert result.flagged_for_crisis is False
    assert list(result.answers_document.values()) == [0] * len(item_ids(instrument))


@pytest.mark.parametrize(
    ("instrument", "total_max"),
    [(PHQ9, 27), (GAD7, 21)],
)
def test_all_threes_is_the_maximum_and_severe(
    instrument: AssessmentInstrument, total_max: int
) -> None:
    result = score(instrument, all_answers(instrument, 3))
    assert result.total == total_max
    assert result.band.id is AssessmentBand.SEVERE
    assert result.band_label == "Severe"


@pytest.mark.parametrize("instrument", [PHQ9, GAD7])
def test_every_reachable_total_maps_to_exactly_one_band(instrument: AssessmentInstrument) -> None:
    """The band table tiles the whole range: no gap, no overlap."""
    seen = [score(instrument, all_answers(instrument, 0))]
    for total in range(0, max(result.max_total for result in seen) + 1):
        matches = [band for band in bands(instrument) if band.min <= total <= band.max]
        assert len(matches) == 1, f"total {total} maps to {len(matches)} bands"


# --------------------------------------------------------------------------- #
# The item-9 rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", [1, 2, 3])
def test_item9_above_zero_flags_whatever_the_total(value: int) -> None:
    result = score(PHQ9, all_answers(PHQ9, 0, phq9_9=value))
    assert result.total == value
    assert result.band.id is AssessmentBand.MINIMAL  # 1, 2 or 3 is a minimal total
    assert result.flagged_for_crisis is True
    assert result.crisis_items == ("phq9_9",)


def test_item9_at_zero_does_not_flag_even_at_a_high_total() -> None:
    result = score(PHQ9, all_answers(PHQ9, 3, phq9_9=0))
    assert result.total == 24
    assert result.band.id is AssessmentBand.SEVERE
    assert result.flagged_for_crisis is False
    assert result.crisis_items == ()


def test_gad7_has_no_item_level_crisis_rule() -> None:
    """GAD-7 has no self-harm item, and the scorer must not invent one."""
    result = score(GAD7, all_answers(GAD7, 3))
    assert result.flagged_for_crisis is False


# --------------------------------------------------------------------------- #
# Missing, unknown and invalid answers
# --------------------------------------------------------------------------- #


def test_missing_answers_are_rejected_by_name() -> None:
    answers = all_answers(PHQ9, 1)
    del answers["phq9_3"]
    del answers["phq9_7"]
    with pytest.raises(IncompleteAssessmentError) as caught:
        score(PHQ9, answers)
    assert caught.value.missing == ("phq9_3", "phq9_7")
    assert "phq9_3" in str(caught.value)


def test_empty_answers_are_rejected_as_missing() -> None:
    with pytest.raises(IncompleteAssessmentError) as caught:
        score(GAD7, {})
    assert len(caught.value.missing) == 7


def test_unknown_items_are_rejected() -> None:
    answers = all_answers(GAD7, 0)
    answers["gad7_8"] = 2
    with pytest.raises(UnknownItemError) as caught:
        score(GAD7, answers)
    assert caught.value.item_ids == ("gad7_8",)


def test_answers_from_the_other_instrument_are_unknown() -> None:
    with pytest.raises(UnknownItemError):
        score(GAD7, all_answers(PHQ9, 0))


@pytest.mark.parametrize("bad", [4, -1, 1.5, "2", None, True, False, [1], {"value": 1}])
def test_answers_that_are_not_the_four_options_are_rejected(bad: object) -> None:
    """``True`` is in this list on purpose: in Python it *is* the integer 1."""
    answers: dict[str, object] = dict(all_answers(PHQ9, 0))
    answers["phq9_4"] = bad
    with pytest.raises(InvalidAnswerError) as caught:
        score(PHQ9, answers)
    assert caught.value.item_id == "phq9_4"


def test_the_input_mapping_is_never_mutated() -> None:
    answers = all_answers(PHQ9, 2)
    before = dict(answers)
    score(PHQ9, answers)
    assert answers == before


def test_answer_order_does_not_change_the_result() -> None:
    first = score(PHQ9, all_answers(PHQ9, 1, phq9_2=3))
    shuffled: Mapping[str, int] = dict(reversed(list(first.answers_document.items())))
    second = score(PHQ9, shuffled)
    assert first.answers_document == second.answers_document
    assert first.total == second.total
    assert first.band.id is second.band.id


# --------------------------------------------------------------------------- #
# A property, over generated answer sets
# --------------------------------------------------------------------------- #

answer_value = st.integers(min_value=0, max_value=3)


@given(
    st.lists(
        answer_value,
        min_size=1,
        max_size=9,
    )
)
def test_total_is_the_sum_and_the_band_contains_it(values: list[int]) -> None:
    """Any complete answer set: the total is the sum, and one band holds it."""
    answers = dict(zip(item_ids(PHQ9), values, strict=False))
    if len(answers) != len(item_ids(PHQ9)):
        return  # the generated list was short; completeness is tested above
    result = score(PHQ9, answers)
    assert result.total == sum(values)
    assert result.band.min <= result.total <= result.band.max
    assert result.band.id is band_for_containing(PHQ9, result.total)


def band_for_containing(instrument: AssessmentInstrument, total: int) -> AssessmentBand:
    matches = [band for band in bands(instrument) if band.min <= total <= band.max]
    assert len(matches) == 1
    return matches[0].id
