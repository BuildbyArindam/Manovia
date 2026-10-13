"""The safety label scheme and the cost matrix that makes training cost-sensitive.

Everything about *what the levels mean* and *what a mistake costs* lives here, in
one place, because the two questions are the same question: a cost matrix is a
written-down statement about which errors this product is willing to make.

The scheme is the one the rules engine already uses
(``app.services.safety.base.RiskLevel``): five ordered levels, ``none`` the least
severe and ``imminent`` the most. ``ml/`` deliberately re-declares the names in
lower case rather than importing the backend: the ML package must install and
run without the backend checkout (see ``ml/pyproject.toml``'s ``app.*`` mypy
override), and a test asserts the two lists agree wherever both are present.

The cost matrix is documented, not derived. Its one shape is deliberate:
**missing a HIGH or IMMINENT message costs far more than over-flagging a benign
one**, and the penalty grows with the size of the miss. That is the whole point
of Day 33 — a plain accuracy objective treats "called a crisis NONE" and "called
a benign message IMMINENT" as equally wrong, which is the opposite of what this
product needs.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "CATEGORIES",
    "COST_MATRIX",
    "COST_MATRIX_DOC",
    "CRISIS_LEVELS",
    "LEVELS",
    "LEVEL_INDEX",
    "MEDIUM_INDEX",
    "categorise",
    "class_weights_from_cost",
    "cost_of",
    "expected_cost_level",
    "level_index",
    "severity",
]

#: The five levels, least severe first. The order *is* the severity order, so
#: every comparison in this package is an index comparison.
LEVELS: Final[tuple[str, ...]] = ("none", "low", "medium", "high", "imminent")

#: Levels at or above which the crisis policy applies (a helpline, not a chat).
CRISIS_LEVELS: Final[frozenset[str]] = frozenset({"high", "imminent"})

#: The categories the eval dataset uses (``evals/build_crisis_dataset.py``).
#: ``none`` is the benign bucket. A model never predicts a category — the rules
#: engine owns categories (ADR 0009) — but the training rows carry one so the
#: cost matrix can be *reported* per family.
CATEGORIES: Final[tuple[str, ...]] = (
    "none",
    "suicidal_ideation",
    "intent_plan",
    "self_harm",
    "severe_hopelessness",
    "acute_medical",
    "abuse_disclosure",
    "harm_to_others",
)

LEVEL_INDEX: Final[dict[str, int]] = {label: index for index, label in enumerate(LEVELS)}

#: Index of ``medium`` — the level the abstention policy resolves to.
MEDIUM_INDEX: Final[int] = LEVEL_INDEX["medium"]


def level_index(label: str) -> int:
    """Severity index of a level name; ``-1`` for anything unknown."""
    return LEVEL_INDEX.get(label.strip().lower(), -1)


def severity(label: str) -> int:
    """Alias of :func:`level_index` that reads better at call sites."""
    return level_index(label)


#: The cost matrix: ``COST_MATRIX[true][predicted]``.
#:
#: Reading the diagonal (0): being right is free. Reading off it: every mistake
#: is priced by how far apart the two levels are, and the prices are not
#: symmetric — calling a crisis ``none`` (25) is dearer than calling a benign
#: message ``imminent`` (20), and both dwarf the price of confusing two
#: adjacent benign levels (1-3).
#:
#: The asymmetry is the product decision, not a modelling trick: a false alarm
#: shows somebody a warm message and a helpline number, a miss can be fatal
#: (``docs/safety-design.md`` §12.6). It is stated here rather than implied by a
#: ``class_weight="balanced"`` so that a reviewer can argue with the number.
COST_MATRIX: Final[dict[str, dict[str, float]]] = {
    "none": {"none": 0.0, "low": 1.0, "medium": 3.0, "high": 8.0, "imminent": 20.0},
    "low": {"none": 1.0, "low": 0.0, "medium": 2.0, "high": 6.0, "imminent": 16.0},
    "medium": {"none": 4.0, "low": 2.0, "medium": 0.0, "high": 5.0, "imminent": 12.0},
    "high": {"none": 12.0, "low": 8.0, "medium": 5.0, "high": 0.0, "imminent": 3.0},
    "imminent": {"none": 25.0, "low": 16.0, "medium": 10.0, "high": 4.0, "imminent": 0.0},
}

#: The same matrix as a list of rows in ``LEVELS`` order — the shape the torch
#: loss and the report writer want. Kept derived so the two can never disagree.
COST_MATRIX_DOC: Final[str] = (
    "cost[true][predicted], levels ordered none < low < medium < high < imminent:\n"
    + "\n".join(
        f"  true {true:>9}: " + "  ".join(f"{COST_MATRIX[true][pred]:>5.1f}" for pred in LEVELS)
        for true in LEVELS
    )
    + "\nA miss of a HIGH/IMMINENT message (rows high/imminent, columns none/low) costs "
    "12-25; an over-flag of a benign one (rows none/low, columns high/imminent) costs 6-20. "
    "Confusing two adjacent benign levels costs 1-3."
)


def cost_of(true: str, predicted: str) -> float:
    """The price of calling a ``true`` message ``predicted``."""
    return COST_MATRIX[true][predicted]


def class_weights_from_cost() -> dict[str, float]:
    """Per-true-class weight = the mean cost of being wrong about that class.

    This is the scalar a plain ``class_weight`` can express: how badly it hurts
    to be wrong about a class at all. It is *not* what the loss uses — the loss
    uses the full matrix — but it is the number a reviewer can sanity-check
    ("is being wrong about IMMINENT really 4x worse than about NONE?"), and it is
    what a ``class_weighting: balanced``-style baseline would approximate.
    """
    return {
        true: round(sum(row[pred] for pred in LEVELS) / len(LEVELS), 4)
        for true, row in COST_MATRIX.items()
    }


def expected_cost_level(
    probabilities: dict[str, float] | dict[str, float],
    matrix: dict[str, dict[str, float]] | None = None,
) -> str:
    """The Bayes decision under the cost matrix: the level with the lowest expected cost.

    ``argmin_p sum_true P(true) * cost[true][p]``. This is the decision rule a
    cost-sensitive classifier is *for*: with the matrix above, a distribution
    that puts 0.30 on ``high`` and 0.70 on ``none`` still predicts ``high``,
    because the expected cost of calling it ``none`` (0.7 * 12) exceeds the cost
    of calling it ``high`` (0.7 * 8 + 0.3 * 0). A plain argmax would say ``none``.
    """
    costs = matrix or COST_MATRIX
    best_level = LEVELS[0]
    best_cost = float("inf")
    for predicted in LEVELS:
        total = sum(
            float(probabilities.get(true, 0.0)) * costs[true][predicted] for true in LEVELS
        )
        if total < best_cost - 1e-12:
            best_cost = total
            best_level = predicted
    return best_level


def categorise(label: str) -> str:
    """Map a level onto the eval dataset's category vocabulary for reporting."""
    return "none" if label == "none" else "risk"
