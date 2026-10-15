"""Reciprocal-rank fusion: how the lexical and the dense half agree.

RRF (Cormack, Clarke & Buettcher, SIGIR 2009) scores a document by *where each
system ranked it*, not by what each system's score was::

    fused(d) = Σ_systems  weight_system / (k + rank_system(d))

That is the reason to use it here rather than a weighted sum of scores: a BM25
score and a cosine similarity are not the same kind of number, they are not on
the same scale, and their scales move with the query (a long query inflates
BM25; a rare-word query inflates cosine). Normalising them into one another is a
per-query calibration problem nobody wants to own. Ranks are already normalised.

Two details that matter for the abstain decision:

* ``k`` (default 60) is the damping constant from the paper. It is a config knob,
  not a constant, because "usual" is not evidence — but the Day-35 tuning found
  the dev optimum flat between 40 and 100, so the default stands.
* :attr:`FusedCandidate.confidence` rescales the fused score into ``[0, 1]`` by
  multiplying by ``k + 1``. A document ranked first by *both* systems scores
  exactly 1.0; anything less is strictly smaller. That makes the fused score a
  threshold-able quantity, which a raw RRF score (~0.016) is not — and the
  "no good answer" calibration needs one number it can put a floor under
  without knowing the corpus size.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

__all__ = ["FusedCandidate", "reciprocal_rank_fusion"]


@dataclass(frozen=True)
class FusedCandidate:
    """One document/chunk after fusion, with the evidence that put it there."""

    id: str
    #: Weighted RRF score.
    score: float
    #: ``score * (k + 1)`` — in [0, 1], 1.0 only for a unanimous first place.
    confidence: float
    #: system name -> 1-based rank, for the failure analysis and the report.
    ranks: dict[str, int] = field(default_factory=dict)


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[str]],
    *,
    weights: Mapping[str, float] | None = None,
    k: int = 60,
) -> list[FusedCandidate]:
    """Fuse several ranked id lists into one.

    ``weights`` defaults to an even split. Weights are renormalised to sum to 1,
    so ``(dense=0.7, bm25=0.3)`` and ``(dense=7, bm25=3)`` are the same setting
    — one less way for a config to be silently wrong. Ties break on the id, so
    the output is a pure function of the input.
    """

    if k < 1:
        raise ValueError("k must be positive")
    if not rankings:
        return []
    resolved = _resolve_weights(rankings, weights)

    totals: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for system, ids in rankings.items():
        weight = resolved[system]
        if weight <= 0.0:
            continue
        for position, chunk_id in enumerate(ids, start=1):
            totals[chunk_id] = totals.get(chunk_id, 0.0) + weight / (k + position)
            ranks.setdefault(chunk_id, {})[system] = position

    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return [
        FusedCandidate(id=chunk_id, score=score, confidence=score * (k + 1), ranks=ranks[chunk_id])
        for chunk_id, score in ordered
    ]


def _resolve_weights(
    rankings: Mapping[str, Sequence[str]], weights: Mapping[str, float] | None
) -> dict[str, float]:
    resolved = {system: float((weights or {}).get(system, 1.0)) for system in rankings}
    for system, weight in resolved.items():
        if weight < 0.0:
            raise ValueError(f"weight for {system!r} must not be negative")
    total = sum(resolved.values())
    if total <= 0.0:
        raise ValueError("at least one system needs a positive weight")
    return {system: weight / total for system, weight in resolved.items()}
