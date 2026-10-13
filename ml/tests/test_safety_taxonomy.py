"""The cost matrix: what a mistake costs, and the arithmetic that follows from it.

Every number here is a decision somebody has to be able to defend, so the tests
pin the shape of the matrix (a missed IMMINENT costs more than a false alarm on
NONE, by a factor the report can quote) and the two things derived from it: the
expected-cost decision rule and the class weights a "balanced" baseline would
approximate.
"""

from __future__ import annotations

import math

import pytest

from manovia_ml.training.safety.taxonomy import (
    COST_MATRIX,
    COST_MATRIX_DOC,
    LEVEL_INDEX,
    LEVELS,
    class_weights_from_cost,
    cost_of,
    expected_cost_level,
    level_index,
    severity,
)

# --------------------------------------------------------------------------- #
# The matrix itself                                                           #
# --------------------------------------------------------------------------- #


def test_the_matrix_is_square_and_covers_every_level() -> None:
    assert set(COST_MATRIX) == set(LEVELS)
    for row in COST_MATRIX.values():
        assert set(row) == set(LEVELS)


def test_being_right_is_free() -> None:
    for level in LEVELS:
        assert COST_MATRIX[level][level] == 0.0


def test_missing_a_crisis_costs_more_than_a_false_alarm() -> None:
    """The one asymmetry the whole day is about.

    The gap is deliberately narrow (25 vs 20): both are expensive, and the point
    of the matrix is not "misses are 10x worse" but "misses are worse, and the
    report can say by how much". A reviewer who wants a wider gap has one number
    to argue with.
    """

    missed_imminent = COST_MATRIX["imminent"]["none"]
    false_alarm = COST_MATRIX["none"]["imminent"]
    assert missed_imminent > false_alarm
    assert missed_imminent == max(cost for row in COST_MATRIX.values() for cost in row.values())


def test_under_calling_is_monotonic_in_severity() -> None:
    """Predicting NONE gets worse the more serious the truth was."""

    costs = [COST_MATRIX[level]["none"] for level in LEVELS]
    assert costs == sorted(costs)
    assert costs[-1] == max(costs)


def test_over_calling_is_monotonic_in_severity_too() -> None:
    """Predicting IMMINENT gets worse the more benign the truth was."""

    costs = [COST_MATRIX["none"][level] for level in LEVELS]
    assert costs == sorted(costs)


def test_the_documented_matrix_is_the_matrix() -> None:
    """``COST_MATRIX_DOC`` is what the report prints; it must not drift.

    Every value in the matrix has to appear in the printed text, or the report is
    quoting a matrix that is not the one that was trained on.
    """

    for level in LEVELS:
        assert level in COST_MATRIX_DOC
    for row in COST_MATRIX.values():
        for value in row.values():
            assert f"{value:>5.1f}" in COST_MATRIX_DOC, value
    assert "12-25" in COST_MATRIX_DOC  # the summary line's crisis-miss range


# --------------------------------------------------------------------------- #
# Severity                                                                     #
# --------------------------------------------------------------------------- #


def test_severity_is_ordered_and_total() -> None:
    assert [severity(level) for level in LEVELS] == sorted(severity(level) for level in LEVELS)
    assert severity("imminent") > severity("high") > severity("medium")
    assert severity("medium") > severity("low") > severity("none")


def test_severity_is_case_and_whitespace_insensitive() -> None:
    assert severity("  HIGH ") == severity("high")


def test_an_unknown_level_is_below_every_real_level() -> None:
    """``-1``, not ``0``: an unrecognised label must never read as ``none``.

    A level the model was never trained on is not "the benign answer"; it is
    "no answer", and ordering it below ``none`` means a stray label can never
    win a max() comparison against a real one.
    """

    assert severity("banana") == -1
    assert severity("banana") < severity("none")
    assert level_index("") == -1


def test_level_index_matches_severity_order() -> None:
    assert [LEVEL_INDEX[level] for level in LEVELS] == [severity(level) for level in LEVELS]


# --------------------------------------------------------------------------- #
# Expected cost                                                                #
# --------------------------------------------------------------------------- #


def test_being_right_is_free_and_being_wrong_is_not() -> None:
    for level in LEVELS:
        assert cost_of(level, level) == 0.0
        for other in LEVELS:
            if other != level:
                assert cost_of(level, other) > 0.0


def test_missing_an_imminent_costs_the_most() -> None:
    """The point of the exercise: this is the error the loss is shaped to avoid."""

    missed = cost_of("imminent", "none")
    over_called = cost_of("none", "imminent")
    wrong_tier = cost_of("imminent", "high")

    assert missed == max(cost_of(true, "none") for true in LEVELS)
    assert missed > over_called > 0.0
    # Being one tier too low is cheaper than being all the way at none.
    assert wrong_tier < missed


def test_expected_cost_of_an_empty_distribution_is_a_level_not_a_crash() -> None:
    """No probabilities at all falls back to the least severe level."""

    assert expected_cost_level({}) == "none"


def test_expected_cost_ignores_unknown_labels_as_the_cheapest_mistake() -> None:
    """A label outside the matrix is not a reason to raise inside a training loop."""

    assert expected_cost_level({"banana": 1.0}) == "none"


# --------------------------------------------------------------------------- #
# Derived quantities                                                           #
# --------------------------------------------------------------------------- #


def test_class_weights_put_the_most_weight_on_imminent() -> None:
    weights = class_weights_from_cost()

    assert set(weights) == set(LEVELS)
    assert weights["imminent"] == max(weights.values())
    assert min(weights.values()) > 0.0


def test_class_weights_are_finite_and_sane() -> None:
    for level, weight in class_weights_from_cost().items():
        assert math.isfinite(weight), level
        assert weight < 100.0, level


def test_class_weights_are_the_mean_cost_of_being_wrong() -> None:
    """Derived, not typed: the docstring's claim is checkable."""

    weights = class_weights_from_cost()
    for level in LEVELS:
        expected = round(sum(COST_MATRIX[level][pred] for pred in LEVELS) / len(LEVELS), 4)
        assert weights[level] == pytest.approx(expected)


def test_the_balanced_approximation_is_a_real_approximation() -> None:
    """``class_weighting: balanced`` in a config is the scalar version of this.

    The weights are derived from the matrix, so a baseline trained with them is
    comparable to the cost-sensitive transformer rather than a different model
    with the same name.
    """

    weights = class_weights_from_cost()
    assert weights["imminent"] > weights["none"]
