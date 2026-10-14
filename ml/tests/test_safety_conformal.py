"""The abstention policy's golden vectors — and the mirror that keeps them honest.

The MEDIUM floor is implemented **twice**: here, in
``manovia_ml.training.safety.conformal.abstain_level``, and in
``backend/app/services/safety/trained.py::_abstain_level``. Two copies of a safety
rule are one rule only while something checks they agree, so the vectors below are
duplicated *verbatim* in ``backend/tests/safety/test_trained_classifier.py`` and
the backend test parses **this file** to run the comparison — a reader editing one
side without the other fails a test rather than shipping a divergence.

The vectors are chosen to cover the three properties the policy claims, and the
one it explicitly refuses:

* it never lowers the point prediction;
* it never lets ambiguity between benign levels resolve downward;
* a set containing MEDIUM or above raises to at least MEDIUM;
* it does **not** promote to the most severe level in the set — ``{medium,
  imminent}`` acts as MEDIUM, not IMMINENT.
"""

from __future__ import annotations

import pytest

from manovia_ml.training.safety.conformal import abstain_level

#: ``(probabilities, quantile) -> expected prediction set``. The quantile is the
#: nonconformity threshold: a level is in the set when its score is at or below it.
#: The membership rule is ``p >= 1 - quantile``, which is why the values below are
#: written as round numbers.
SET_CASES: tuple[tuple[dict[str, float], float, frozenset[str]], ...] = (
    # A confident prediction is a singleton.
    (
        {"none": 0.01, "low": 0.02, "medium": 0.03, "high": 0.10, "imminent": 0.84},
        0.20,
        frozenset({"imminent"}),
    ),
    # A torn one keeps every level the model could not rule out: at q=0.75 the
    # membership rule is p >= 0.25, so medium (0.20) is ruled out and the two
    # crisis levels are not.
    (
        {"none": 0.10, "low": 0.15, "medium": 0.20, "high": 0.30, "imminent": 0.25},
        0.75,
        frozenset({"high", "imminent"}),
    ),
    # A uniform distribution keeps everything.
    (
        {"none": 0.20, "low": 0.20, "medium": 0.20, "high": 0.20, "imminent": 0.20},
        0.95,
        frozenset({"none", "low", "medium", "high", "imminent"}),
    ),
    # The argmax is always in the set, even when nothing clears the quantile.
    (
        {"none": 0.90, "low": 0.04, "medium": 0.03, "high": 0.02, "imminent": 0.01},
        0.05,
        frozenset({"none"}),
    ),
)

#: ``(set, point) -> expected abstention level``. Shared with the backend test,
#: which parses this file so the two implementations cannot drift apart silently.
ABSTAIN_CASES: tuple[tuple[frozenset[str], str, str], ...] = (
    # Ambiguity between two benign levels is not an escalation.
    (frozenset({"none", "low"}), "none", "none"),
    (frozenset({"none", "low"}), "low", "low"),
    # A risky set raises to at least MEDIUM, whatever the point prediction says.
    (frozenset({"none", "medium"}), "none", "medium"),
    (frozenset({"low", "high"}), "low", "medium"),
    (frozenset({"medium"}), "medium", "medium"),
    (frozenset({"medium", "imminent"}), "none", "medium"),
    (frozenset({"medium", "imminent"}), "imminent", "imminent"),
    (frozenset({"high", "medium"}), "high", "high"),
    (frozenset({"none", "high"}), "none", "medium"),
    # A singleton crisis set is just the point prediction.
    (frozenset({"high"}), "high", "high"),
    (frozenset({"imminent"}), "imminent", "imminent"),
    (frozenset({"none"}), "none", "none"),
    (frozenset({"low"}), "low", "low"),
)


@pytest.mark.parametrize(("probabilities", "quantile", "expected"), SET_CASES)
def test_the_prediction_set_keeps_what_the_quantile_allows(
    probabilities: dict[str, float], quantile: float, expected: frozenset[str]
) -> None:
    from manovia_ml.training.safety.conformal import prediction_set

    assert prediction_set(probabilities, quantile) == expected


@pytest.mark.parametrize(("prediction", "point", "expected"), ABSTAIN_CASES)
def test_the_abstention_policy_is_the_medium_floor(
    prediction: frozenset[str], point: str, expected: str
) -> None:
    assert abstain_level(prediction, point=point) == expected


def test_the_policy_never_lowers_the_point_prediction() -> None:
    """The property the ensemble's `max(rules, ml)` also depends on."""

    for _prediction, point, expected in ABSTAIN_CASES:
        from manovia_ml.training.safety.taxonomy import severity

        assert severity(expected) >= severity(point)


def test_the_policy_does_not_promote_to_the_worst_plausible_level() -> None:
    """`{medium, imminent}` acts as MEDIUM. Stated separately because it is the
    part a reader will assume is wrong."""

    assert abstain_level(frozenset({"medium", "imminent"}), point="none") == "medium"


def test_the_vectors_are_the_ones_the_backend_duplicates() -> None:
    """A guard on the guard: the backend parses this file, so the shape matters.

    If someone renames ``ABSTAIN_CASES`` or changes it to a dict, the backend's
    parser stops finding it — and the mirror silently becomes a no-op. This fails
    first, in the repo that owns the vectors.
    """

    import re
    from pathlib import Path

    source = Path(__file__).read_text(encoding="utf-8")
    assert re.search(r"^ABSTAIN_CASES: tuple\[", source, re.MULTILINE), (
        "ABSTAIN_CASES must stay a module-level tuple literal: the backend test "
        "parses it out of this file to keep the two implementations in step"
    )
    assert all(len(case) == 3 for case in ABSTAIN_CASES)
