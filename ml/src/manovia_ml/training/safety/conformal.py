"""Split conformal prediction sets, and the abstain -> be cautious policy.

The ensemble already has an "uncertain -> treat as MEDIUM" band, but it is a
threshold somebody chose. This module is the same idea with a guarantee attached:

    for any model, any distribution, if the nonconformity scores are computed on
    a held-out calibration split, the prediction set covers the true level with
    probability >= 1 - alpha (Vovk et al. 2005; Lei & Wasserman 2014).

**The score.** ``s(x, y) = 1 - p_hat(y | x)`` over the *temperature-scaled*
probabilities. A level the model is sure of scores near 0; a level it never
considered scores near 1. The set is every level whose score is at or below the
calibration quantile, always including the argmax so the set is never empty.

**The policy.** If the set contains any level at or above MEDIUM, the message is
treated as MEDIUM or above. That is the formal "abstain -> be cautious" rule the
brief asks for: an ambiguous answer that *might* be a crisis is not allowed to
resolve downward into "nothing to see here". When the set is entirely below
MEDIUM the point prediction stands — there is nothing to be cautious about.

**What it does not do.** It does not fix a badly calibrated model; it converts
calibration into a coverage statement. If the model's probabilities are noise,
the sets simply get large, and the abstention rate goes up — which is the honest
outcome, and the reason the abstention rate is reported next to the coverage
rather than hidden.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from manovia_ml.training.safety.taxonomy import LEVELS, severity

__all__ = [
    "DEFAULT_ALPHA",
    "ConformalResult",
    "abstain_level",
    "conformal_quantile",
    "nonconformity_scores",
    "prediction_set",
    "set_coverage",
    "set_size_stats",
]


#: Default miscoverage rate: 95% coverage.
DEFAULT_ALPHA: Final[float] = 0.05


def nonconformity_scores(
    probabilities: Sequence[Mapping[str, float]], labels: Sequence[str]
) -> list[float]:
    """``1 - p(true)`` per row: how badly the model did on the true level."""
    out: list[float] = []
    for row, label in zip(probabilities, labels, strict=True):
        key = label.strip().lower()
        out.append(1.0 - float(row.get(key, 0.0)))
    return out


def conformal_quantile(scores: Sequence[float], alpha: float = DEFAULT_ALPHA) -> float:
    """The finite-sample-corrected ``(1 - alpha)`` quantile of the scores.

    ``ceil((n + 1) * (1 - alpha)) / n``-th order statistic. The ``+1`` is the
    correction that makes the coverage guarantee hold at finite ``n`` instead of
    only asymptotically; with a small calibration split it is the difference
    between "about 95%" and "at least 95%".
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if not scores:
        raise ValueError("conformal calibration needs at least one score")
    ordered = sorted(scores)
    n = len(ordered)
    rank = math.ceil((n + 1) * (1.0 - alpha))
    index = min(max(rank, 1), n) - 1
    return ordered[index]


def prediction_set(
    probabilities: Mapping[str, float], quantile: float
) -> frozenset[str]:
    """Every level whose nonconformity score is at or below ``quantile``.

    The argmax is always included, so a perfectly confident prediction yields a
    singleton and a genuinely torn one yields the levels the model could not
    rule out. A set with more than one element is an **abstention**: the model is
    declining to name one level.
    """
    members = {
        label
        for label in LEVELS
        if 1.0 - float(probabilities.get(label, 0.0)) <= quantile + 1e-12
    }
    if not members:
        best = max(LEVELS, key=lambda label: float(probabilities.get(label, 0.0)))
        members = {best}
    return frozenset(members)


def set_coverage(sets: Sequence[frozenset[str]], labels: Sequence[str]) -> float:
    """Share of rows whose true level is inside the prediction set."""
    if not sets:
        return 0.0
    hits = sum(
        1
        for item, label in zip(sets, labels, strict=True)
        if label.strip().lower() in item
    )
    return hits / len(sets)


def set_size_stats(sets: Sequence[frozenset[str]]) -> dict[str, float]:
    """Mean, max and singleton share of the set sizes — the abstention picture."""
    if not sets:
        return {"mean_size": 0.0, "max_size": 0.0, "singleton_share": 0.0, "abstention_rate": 0.0}
    sizes = [len(item) for item in sets]
    singletons = sum(1 for size in sizes if size == 1)
    return {
        "mean_size": sum(sizes) / len(sizes),
        "max_size": float(max(sizes)),
        "singleton_share": singletons / len(sizes),
        # An abstention is exactly a non-singleton set: the model declined to
        # name one level, and the policy below decides what that costs.
        "abstention_rate": 1.0 - singletons / len(sets),
    }


def abstain_level(prediction: frozenset[str], *, point: str) -> str:
    """The level the system acts on, given the set and the point prediction.

    If the set holds any level at or above MEDIUM, the answer is MEDIUM or above
    — never below the point prediction, and never below MEDIUM. Otherwise the
    point prediction stands: a set of ``{none, low}`` is ambiguity between two
    benign levels, and there is nothing to be cautious about.
    """
    risky = [level for level in prediction if severity(level) >= severity("medium")]
    if not risky:
        return point
    cautious = max(risky, key=severity)
    return max((point, cautious), key=severity)


@dataclass(frozen=True)
class ConformalResult:
    """The fitted quantile and what the sets did on each split."""

    alpha: float
    quantile: float
    calibration_rows: int
    calibration_coverage: float
    target_coverage: float
    #: Coverage on a split the quantile was *not* fitted on. The honest number.
    heldout_coverage: float = 0.0
    heldout_rows: int = 0
    abstention_rate: float = 0.0
    mean_set_size: float = 0.0
    raised_by_abstention: int = 0
    evaluated_rows: int = 0
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def meets_target(self) -> bool:
        """Whether the held-out coverage cleared the target (the guarantee)."""
        return self.heldout_coverage >= self.target_coverage - 1e-9

    def as_dict(self) -> dict[str, Any]:
        return {
            "alpha": self.alpha,
            "target_coverage": round(self.target_coverage, 4),
            "quantile": round(self.quantile, 6),
            "calibration_rows": self.calibration_rows,
            "calibration_coverage": round(self.calibration_coverage, 4),
            "heldout_rows": self.heldout_rows,
            "heldout_coverage": round(self.heldout_coverage, 4),
            "meets_target": self.meets_target,
            "abstention_rate": round(self.abstention_rate, 4),
            "mean_set_size": round(self.mean_set_size, 4),
            "raised_by_abstention": self.raised_by_abstention,
            "evaluated_rows": self.evaluated_rows,
            "notes": self.notes,
        }


def calibrate_conformal(
    probabilities: Sequence[Mapping[str, float]],
    labels: Sequence[str],
    *,
    alpha: float = DEFAULT_ALPHA,
) -> tuple[float, list[frozenset[str]]]:
    """Fit the quantile on the calibration split and return its own sets."""
    scores = nonconformity_scores(probabilities, labels)
    quantile = conformal_quantile(scores, alpha)
    sets = [prediction_set(row, quantile) for row in probabilities]
    return quantile, sets
