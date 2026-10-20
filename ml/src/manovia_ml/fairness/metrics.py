"""Fairness gap metrics: paired-sentence invariance."""

from __future__ import annotations

from dataclasses import dataclass
import statistics


@dataclass(frozen=True)
class GapResult:
    template_id: str
    category: str
    scores: list[float]
    max_gap: float
    mean_gap: float
    violation: bool
    threshold: float

    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "category": self.category,
            "scores": self.scores,
            "max_gap": self.max_gap,
            "mean_gap": self.mean_gap,
            "violation": self.violation,
            "threshold": self.threshold,
        }


def compute_gap(
    template_id: str,
    category: str,
    scores: list[float],
    threshold: float,
) -> GapResult:
    """Compute max and mean absolute gaps across variants.

    Gap definition: max absolute difference between any two variant scores
    (prediction must not change materially when identity term is swapped).
    For emotion or safety scores in [0,1], a gap of 0.10 == 10 points.

    Also computes mean pairwise gap for diagnostics.
    """
    if len(scores) < 2:
        return GapResult(
            template_id=template_id,
            category=category,
            scores=scores,
            max_gap=0.0,
            mean_gap=0.0,
            violation=False,
            threshold=threshold,
        )
    gaps: list[float] = []
    for i in range(len(scores)):
        for j in range(i + 1, len(scores)):
            gaps.append(abs(scores[i] - scores[j]))
    max_gap = max(gaps) if gaps else 0.0
    mean_gap = statistics.mean(gaps) if gaps else 0.0
    violation = max_gap > threshold
    return GapResult(
        template_id=template_id,
        category=category,
        scores=scores,
        max_gap=max_gap,
        mean_gap=mean_gap,
        violation=violation,
        threshold=threshold,
    )
