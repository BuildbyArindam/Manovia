"""Calibration and conformal prediction: two guarantees, and what they are not.

Temperature scaling buys *calibration* — a 0.7 means 0.7 — and the reliability
diagram is how that claim is checked rather than asserted. Split conformal buys
*coverage* — the prediction set contains the true level at least 95% of the time
— and the abstention policy turns a wide set into "treat as MEDIUM or above".

Both are fitted on the **calibration** split and nowhere else, so the tests here
are about the fitting discipline as much as the arithmetic: a temperature fitted
on the held-out data is a leak, and a coverage number reported on the data the
quantile was fitted on is a tautology.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from manovia_ml.training.safety.calibration import (
    CalibrationResult,
    apply_temperature,
    calibrate,
    expected_calibration_error,
    fit_temperature,
    reliability_diagram,
    softmax,
)
from manovia_ml.training.safety.conformal import (
    abstain_level,
    conformal_quantile,
    nonconformity_scores,
    prediction_set,
    set_coverage,
    set_size_stats,
)
from manovia_ml.training.safety.metrics import bootstrap_ci
from manovia_ml.training.safety.taxonomy import LEVEL_INDEX, LEVELS

#: A confidently **wrong** model: huge logits that put their mass on the level
#: above the truth. The softmax saturates at ~1.0 while the accuracy is 2/3,
#: which is exactly the case temperature scaling exists to fix.
CONFIDENT_LOGITS = [
    [9.0, 0.0, 0.0, 0.0, 0.0],
    [0.0, 9.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 0.0, 9.0],
]
CONFIDENT_LABELS = ["none", "low", "medium"]


# --------------------------------------------------------------------------- #
# Softmax and temperature                                                      #
# --------------------------------------------------------------------------- #


def test_softmax_is_a_distribution() -> None:
    values = softmax([1.0, 2.0, 3.0])

    assert sum(values) == pytest.approx(1.0)
    assert values == sorted(values)


def test_temperature_above_one_flattens_the_distribution() -> None:
    logits = [2.0, 1.0, 0.5]

    sharp = apply_temperature(logits, 0.5)
    flat = apply_temperature(logits, 4.0)

    assert max(sharp) > max(flat)
    assert min(flat) > min(sharp)


def test_a_temperature_of_one_is_the_identity() -> None:
    logits = [2.0, 1.0, 0.5]

    assert apply_temperature(logits, 1.0) == pytest.approx(softmax(logits))


@pytest.mark.parametrize("temperature", [0.0, -1.0])
def test_an_unusable_temperature_is_rejected(temperature: float) -> None:
    """``T <= 0`` would flip the ranking; it must never be accepted silently."""

    with pytest.raises(ValueError):
        apply_temperature([1.0, 2.0], temperature)


#: ``fit_temperature`` takes label *indices* (it is the inner loop ``calibrate``
#: calls), so the fixtures below name the index of the true level.
CONFIDENT_INDICES = [0, 1, 2]


def test_fit_temperature_softens_an_over_confident_model() -> None:
    """The whole point: the fitted T must be > 1 for a saturated softmax."""

    temperature = fit_temperature(CONFIDENT_LOGITS, CONFIDENT_INDICES)

    assert temperature > 1.0


def test_fit_temperature_leaves_a_well_separated_model_sharp() -> None:
    """Temperature scaling is a correction, not a habit.

    When every row's argmax is the truth and the margin is real, the NLL is
    already low and the search does not soften anything: it may sharpen. The
    failure mode worth guarding against is a fitter that *always* returns
    ``T > 1`` because it only knows how to soften.
    """

    temperature = fit_temperature(
        [[0.4, 0.2, 0.1], [0.1, 0.5, 0.2], [0.2, 0.1, 0.6]], CONFIDENT_INDICES
    )

    assert temperature <= 1.0


def test_fit_temperature_needs_labels() -> None:
    with pytest.raises(ValueError):
        fit_temperature(CONFIDENT_LOGITS, [])


def test_fit_temperature_rejects_an_out_of_range_index() -> None:
    with pytest.raises(ValueError):
        fit_temperature(CONFIDENT_LOGITS, [0, 1, 99])


# --------------------------------------------------------------------------- #
# ECE and the reliability diagram                                              #
# --------------------------------------------------------------------------- #


def test_ece_is_zero_for_a_perfectly_calibrated_model() -> None:
    """One bin, confidence exactly equal to accuracy."""

    probabilities = [{"none": 1.0} for _ in range(10)]
    labels = ["none"] * 10

    assert expected_calibration_error(probabilities, labels, bins=5) == pytest.approx(0.0)


def test_ece_is_large_for_an_over_confident_wrong_model() -> None:
    probabilities = [{"none": 0.99} for _ in range(10)]
    labels = ["imminent"] * 10

    error = expected_calibration_error(probabilities, labels, bins=5)

    assert error > 0.9


def test_ece_of_an_empty_set_is_zero_not_a_crash() -> None:
    assert expected_calibration_error([], [], bins=5) == 0.0


def test_the_reliability_diagram_reports_every_bin_with_counts() -> None:
    probabilities = [{"none": 0.95}, {"none": 0.55}, {"none": 0.15}]
    labels = ["none", "none", "low"]

    bins = reliability_diagram(probabilities, labels, bins=5)

    # Empty bins are reported too: a reliability diagram that silently skipped
    # them would hide that the model never speaks with moderate confidence.
    assert len(bins) == 5
    assert sum(item.count for item in bins) == 3
    for item in bins:
        assert 0.0 <= item.confidence <= 1.0
        assert 0.0 <= item.accuracy <= 1.0
        assert item.gap == pytest.approx(item.confidence - item.accuracy)


def test_the_reliability_diagram_gap_is_confidence_minus_accuracy() -> None:
    """A positive gap means over-confident; that is the direction that matters."""

    probabilities = [{"none": 0.9} for _ in range(4)]
    labels = ["none", "none", "low", "low"]

    bins = reliability_diagram(probabilities, labels, bins=5)
    filled = [item for item in bins if item.count]

    assert filled[0].gap > 0


# --------------------------------------------------------------------------- #
# The fitted calibration result                                                #
# --------------------------------------------------------------------------- #


def test_calibrate_reports_before_and_after_on_the_same_split() -> None:
    result = calibrate(CONFIDENT_LOGITS, CONFIDENT_LABELS, bins=5, split="calibration")

    assert isinstance(result, CalibrationResult)
    assert result.split == "calibration"
    assert result.temperature >= 1.0
    assert result.bins
    assert result.temperature >= 1.0
    assert math.isfinite(result.ece_before)
    assert math.isfinite(result.ece_after)


def test_calibrate_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        calibrate(CONFIDENT_LOGITS, ["none"], bins=5)


# --------------------------------------------------------------------------- #
# Conformal prediction sets                                                    #
# --------------------------------------------------------------------------- #


def test_a_confident_prediction_is_a_singleton() -> None:
    probabilities = dict(zip(LEVELS, [0.01, 0.02, 0.03, 0.04, 0.90], strict=True))

    assert prediction_set(probabilities, 0.2) == frozenset({"imminent"})


def test_an_uncertain_prediction_keeps_every_plausible_level() -> None:
    probabilities = dict(zip(LEVELS, [0.2, 0.2, 0.2, 0.2, 0.2], strict=True))

    assert prediction_set(probabilities, 0.85) == frozenset(LEVELS)


def test_the_argmax_is_always_in_the_set() -> None:
    """A set that excluded the model's own answer would be useless."""

    probabilities = dict(zip(LEVELS, [0.9, 0.04, 0.03, 0.02, 0.01], strict=True))

    assert "none" in prediction_set(probabilities, 0.0)


def test_nonconformity_scores_are_one_minus_the_true_probability() -> None:
    probabilities = [dict(zip(LEVELS, [0.8, 0.1, 0.05, 0.03, 0.02], strict=True))]

    assert nonconformity_scores(probabilities, ["none"]) == pytest.approx([0.2])


def test_coverage_counts_a_hit_only_when_the_true_level_is_inside() -> None:
    sets = [frozenset({"none"}), frozenset({"low", "medium"})]

    assert set_coverage(sets, ["none", "medium"]) == 1.0
    assert set_coverage(sets, ["none", "high"]) == 0.5
    assert set_coverage([], []) == 0.0


def test_the_quantile_is_one_of_the_observed_scores() -> None:
    """Finite-sample conformal: an order statistic, never an interpolated fantasy."""

    scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

    quantile = conformal_quantile(scores, alpha=0.1)

    assert quantile in scores


def test_the_quantile_of_a_perfect_model_is_small() -> None:
    """Every calibration score is 0, so almost nothing has to be in the set."""

    assert conformal_quantile([0.0] * 10, alpha=0.05) <= 0.05 + 1e-9


def test_the_quantile_of_a_useless_model_is_large() -> None:
    """Every score is 1, so the set has to hold everything to keep its promise."""

    assert conformal_quantile([1.0] * 10, alpha=0.05) >= 0.95


def test_a_larger_error_budget_gives_a_narrower_set() -> None:
    """``alpha`` is the error the operator accepts, so more of it means less caution."""

    scores = [0.1 * index for index in range(1, 11)]

    strict = conformal_quantile(scores, alpha=0.01)
    loose = conformal_quantile(scores, alpha=0.30)

    assert loose <= strict


def test_the_finite_sample_correction_bites_on_a_small_split() -> None:
    """With 10 rows at alpha=0.05 the rank is ceil(11 * 0.95) = 11, clamped to 10.

    The clamp is the honest failure mode of a small calibration split: the
    guarantee holds, but the sets get wide. That is why the report prints the
    calibration row count next to the coverage.
    """

    scores = [0.1 * index for index in range(1, 11)]

    assert conformal_quantile(scores, alpha=0.05) == max(scores)
    # With 200 rows the same alpha picks a mid-range score instead.
    large = [index / 200 for index in range(1, 201)]
    assert conformal_quantile(large, alpha=0.05) < max(large)


def test_an_unusable_alpha_is_rejected() -> None:
    with pytest.raises(ValueError):
        conformal_quantile([0.1], alpha=0.0)
    with pytest.raises(ValueError):
        conformal_quantile([0.1], alpha=1.0)
    with pytest.raises(ValueError):
        conformal_quantile([], alpha=0.05)


# --------------------------------------------------------------------------- #
# Set sizes and the abstention policy                                          #
# --------------------------------------------------------------------------- #


def test_set_size_stats_report_the_abstention_rate() -> None:
    sets = [frozenset({"none"}), frozenset({"none", "low"}), frozenset(LEVELS)]

    stats = set_size_stats(sets)

    assert stats["mean_size"] == pytest.approx((1 + 2 + 5) / 3)
    assert stats["max_size"] == 5.0
    assert stats["singleton_share"] == pytest.approx(1 / 3)
    assert stats["abstention_rate"] == pytest.approx(2 / 3)


def test_set_size_stats_of_nothing_is_zeroes() -> None:
    stats = set_size_stats([])

    assert stats["mean_size"] == 0.0
    assert stats["abstention_rate"] == 0.0


def test_a_benign_ambiguity_is_not_an_escalation() -> None:
    """``{none, low}`` is ambiguity between two harmless answers."""

    assert abstain_level(frozenset({"none", "low"}), point="none") == "none"
    assert abstain_level(frozenset({"none", "low"}), point="low") == "low"


def test_a_risky_set_raises_to_at_least_medium() -> None:
    assert abstain_level(frozenset({"none", "medium"}), point="none") == "medium"
    assert abstain_level(frozenset({"low", "high"}), point="low") == "medium"


def test_the_policy_never_lowers_the_point_prediction() -> None:
    for point in LEVELS:
        for prediction in (
            frozenset({"none"}),
            frozenset({"none", "low"}),
            frozenset({"low", "high"}),
            frozenset({"medium"}),
            frozenset({"none", "medium", "imminent"}),
        ):
            acted = abstain_level(prediction, point=point)
            assert LEVEL_INDEX[acted] >= LEVEL_INDEX[point]


def test_the_policy_does_not_promote_to_the_worst_plausible_level() -> None:
    """A set of ``{medium, imminent}`` acts as MEDIUM, not IMMINENT.

    The set lists what the model could not rule out. Promoting to the worst of
    those would turn every uncertain row into a crisis card; how far above
    MEDIUM a message goes is the point prediction's job.
    """

    assert abstain_level(frozenset({"medium", "imminent"}), point="none") == "medium"
    assert abstain_level(frozenset({"medium", "imminent"}), point="imminent") == "imminent"


# --------------------------------------------------------------------------- #
# The bootstrap interval                                                       #
# --------------------------------------------------------------------------- #


def test_a_bootstrap_interval_brackets_a_point_estimate() -> None:
    """A metric that is always 1.0 has a degenerate interval: (1.0, 1.0)."""

    truths = ["high", "imminent", "none", "low", "medium"] * 4
    predictions = list(truths)

    interval = bootstrap_ci(
        list(zip(truths, predictions, strict=True)),
        lambda _sample: 1.0,
        resamples=50,
        seed=20261013,
    )

    assert interval == (1.0, 1.0)


def test_the_bootstrap_of_nothing_is_no_interval_not_a_crash() -> None:
    """An empty split has no interval; reporting one would be inventing data."""

    assert bootstrap_ci([], lambda _sample: 1.0, resamples=50) == (None, None)


def test_a_bootstrap_interval_of_a_coin_flip_is_wide() -> None:
    truths = ["high" if index % 2 else "none" for index in range(20)]
    predictions = ["high" if index % 3 else "none" for index in range(20)]

    interval = bootstrap_ci(
        list(zip(truths, predictions, strict=True)),
        lambda sample: sum(1 for a, b in sample if a == b) / len(sample),
        resamples=200,
        seed=20261013,
    )

    low, high = interval
    assert low is not None and high is not None, "a non-empty split has an interval"
    assert 0.0 <= low <= high <= 1.0
    assert high - low > 0.1


def test_the_bootstrap_is_deterministic_for_a_seed() -> None:
    pairs = [(("high" if index % 2 else "none"), "high") for index in range(20)]

    def score(sample: Sequence[tuple[str, str]]) -> float:
        return sum(1 for a, b in sample if a == b) / len(sample)

    first = bootstrap_ci(pairs, score, resamples=100, seed=7)
    second = bootstrap_ci(pairs, score, resamples=100, seed=7)

    assert first == second
    assert first[0] is not None
