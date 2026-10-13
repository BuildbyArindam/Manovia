"""The cost-sensitive objective, in one file.

Two things a cost matrix buys, and this module is both of them:

1. **A training loss.** ``cost_weighted_cross_entropy`` multiplies each class's
   log-probability by ``cost[true][predicted]`` before the cross-entropy sum, so
   the gradient pushes probability *away from the classes that would be
   expensive mistakes for this row*. That is the standard cost-sensitive
   multiclass formulation (Elkan 2001; Zhou & Liu 2006) and it needs no
   resampling, which matters on a small synthetic set where duplicating the rare
   crisis rows would just overfit them.
2. **A decision rule.** ``expected_cost_level`` is the Bayes decision under the
   same matrix, and it is what the artefact uses at inference time. Training with
   the matrix and then deciding with a plain argmax would waste half of it: a
   distribution of 0.30 ``high`` / 0.70 ``none`` is *supposed* to come out
   ``high``.

The torch import is deliberately inside the function: the matrix, the weights
and the documentation are pure Python and the tests exercise them on a machine
with no ``[training]`` extra installed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from manovia_ml.training.safety.taxonomy import (
    COST_MATRIX,
    COST_MATRIX_DOC,
    LEVELS,
    class_weights_from_cost,
    cost_of,
    expected_cost_level,
)

__all__ = [
    "COST_MATRIX",
    "COST_MATRIX_DOC",
    "cost_matrix_rows",
    "cost_of",
    "cost_weighted_cross_entropy",
    "expected_cost_level",
    "row_weight",
    "total_expected_cost",
]

#: Per-row normalisation of the loss. Dividing each row by its own total cost
#: keeps a rare ``imminent`` row (row total 55) from contributing ~1.7x the
#: gradient of a ``none`` row (row total 32) purely because its row is dearer.
#: The *relative* weighting between classes is untouched — only the scale is
#: comparable to a plain cross-entropy, which keeps the learning rate in the
#: config meaningful.
NORMALISE_PER_ROW: Final[bool] = True


def cost_matrix_rows() -> list[list[float]]:
    """``COST_MATRIX`` as rows in ``LEVELS`` order — the torch tensor's shape."""
    return [[COST_MATRIX[true][pred] for pred in LEVELS] for true in LEVELS]


def row_weight(true: str) -> float:
    """Total cost of being wrong about a ``true`` message (its off-diagonal row sum)."""
    return float(sum(COST_MATRIX[true][pred] for pred in LEVELS))


def total_expected_cost(
    probabilities: Mapping[str, float], matrix: Mapping[str, Mapping[str, float]] | None = None
) -> float:
    """Expected cost of the :func:`expected_cost_level` decision for one message."""
    costs = matrix or COST_MATRIX
    chosen = expected_cost_level(dict(probabilities), dict(costs))  # type: ignore[arg-type]
    return float(
        sum(float(probabilities.get(true, 0.0)) * costs[true][chosen] for true in LEVELS)
    )


def cost_weighted_cross_entropy(
    logits: Any,
    targets: Sequence[int],
    *,
    torch: Any,
    matrix: Sequence[Sequence[float]] | None = None,
    label_smoothing: float = 0.0,
    normalise_per_row: bool = NORMALISE_PER_ROW,
) -> Any:
    """Cost-weighted cross-entropy over a batch of logits.

    Parameters
    ----------
    logits:
        ``(batch, classes)`` unnormalised scores, class order = ``LEVELS``.
    targets:
        Integer class indices, same order.
    torch:
        The module, passed in so this function never imports torch itself.
    matrix:
        The cost matrix as rows; defaults to :data:`COST_MATRIX`.
    label_smoothing:
        Optional smoothing, applied to the *targets* before the cost weights are
        looked up, exactly as ``torch`` does it for plain cross-entropy.
    normalise_per_row:
        See :data:`NORMALISE_PER_ROW`.

    Returns
    -------
    A scalar tensor. The mean over the batch of the per-row cost-weighted
    negative log-likelihood.
    """
    rows = [list(row) for row in matrix] if matrix is not None else cost_matrix_rows()
    weights = torch.tensor(rows, dtype=logits.dtype, device=logits.device)
    log_probs = torch.log_softmax(logits, dim=-1)

    index = torch.tensor(list(targets), dtype=torch.long, device=logits.device)
    if label_smoothing > 0.0:
        # Smooth the one-hot target, then look each class's weight up in the
        # matrix: the cost of predicting a class the truth may partly be.
        one_hot = torch.zeros_like(logits).scatter_(1, index.unsqueeze(1), 1.0)
        smoothed = one_hot * (1.0 - label_smoothing) + label_smoothing / logits.shape[-1]
        row_weights = smoothed @ weights.t()
    else:
        row_weights = weights[index]

    per_class = row_weights * -log_probs
    if normalise_per_row:
        denom = row_weights.sum(dim=-1).clamp_min(1e-8)
        per_row = per_class.sum(dim=-1) / denom
    else:
        per_row = per_class.sum(dim=-1)
    return per_row.mean()


def cost_weighted_cross_entropy_python(
    probabilities: Sequence[Mapping[str, float]], truths: Sequence[str]
) -> float:
    """The same loss, in pure Python over probability rows.

    Exists so the cost weighting is testable without torch and so a reviewer can
    hand-check one row: ``-sum_pred cost[true][pred] * log p[pred]``, row-normalised.
    """
    total = 0.0
    for probabilities_row, true in zip(probabilities, truths, strict=True):
        weights = [COST_MATRIX[true][pred] for pred in LEVELS]
        denom = sum(weights) or 1.0
        terms = zip(weights, LEVELS, strict=True)
        row = -sum(
            weight * _log(probabilities_row.get(pred, 0.0)) for weight, pred in terms
        )
        total += row / denom
    return total / max(1, len(truths))


def _log(value: float) -> float:
    import math

    return math.log(max(value, 1e-12))


def documented_class_weights() -> dict[str, float]:
    """The scalar class weights, for the report and the model card."""
    return class_weights_from_cost()
