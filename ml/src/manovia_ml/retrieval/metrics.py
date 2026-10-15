"""Retrieval metrics: what the Day-35 report is allowed to say.

The definitions matter more than the arithmetic, so they are written down here
once and used everywhere:

* **The unit is the document, not the chunk.** The production retriever returns
  one card per document (``KnowledgeRetriever`` dedupes by ``doc_id``), and the
  eval labels are document ids, so a "retrieved list" is a ranked list of
  document ids.
* **``recall@k`` is set-based**: |relevant ∩ top-k| / |relevant|. With 1-3
  relevant documents per query, ``recall@1`` can never reach 1.0 for a
  two-label query, which is why ``hit@k`` ("at least one relevant document in
  the top k") is reported next to it. Both are in every table; quoting one
  without the other is how a retrieval result gets over-read.
* **``mrr`` uses the first relevant document** — the standard definition, and
  the one that matches how the chat surface works (the first card is the one a
  person reads).
* **No-answer queries are excluded from the ranking metrics** (recall and MRR
  are undefined when there is nothing to recall) and handled by
  :class:`AbstainReport` instead. ``n_answerable`` and ``n_no_answer`` are
  always reported next to the numbers so the exclusion is visible.
* **Confidence intervals are row bootstraps**, resampling queries with
  replacement. With 52 test rows a ±0.05 claim is not available, and a report
  that hides that is worse than no report.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "AbstainReport",
    "QueryResult",
    "RankingReport",
    "evaluate_ranking",
    "fit_abstain_threshold",
    "percentile",
    "score_abstain",
]

#: The k values every table reports (the brief's Recall@1/3/5).
DEFAULT_KS: tuple[int, ...] = (1, 3, 5)


@dataclass(frozen=True)
class QueryResult:
    """One system's answer to one eval query, with what it cost."""

    query_id: str
    lang: str
    style: str
    #: The labelled ground truth (empty for a no-answer query).
    relevant: tuple[str, ...]
    #: Ranked document ids the system returned, best first.
    retrieved: tuple[str, ...]
    #: The score of the best candidate *before* any abstain floor was applied:
    #: the number a threshold would be fitted on. ``None`` when the system
    #: produced no candidate at all.
    top_score: float | None = None
    latency_ms: float = 0.0
    #: Free-form reason a run could not produce anything (an embedder that is
    #: unavailable, a store that is empty). Reported, never silently dropped.
    error: str | None = None

    @property
    def has_answer(self) -> bool:
        return bool(self.relevant)


def percentile(values: Sequence[float], q: float) -> float:
    """The q-th percentile (0-100), linear interpolation, no numpy needed."""

    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * (q / 100.0)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[int(position)])
    weight = position - low
    return float(ordered[low] * (1 - weight) + ordered[high] * weight)


def _recall_at(relevant: Sequence[str], retrieved: Sequence[str], k: int) -> float:
    if not relevant:
        return 0.0
    top = set(retrieved[:k])
    return len(top & set(relevant)) / len(set(relevant))


def _hit_at(relevant: Sequence[str], retrieved: Sequence[str], k: int) -> float:
    if not relevant:
        return 0.0
    return 1.0 if set(retrieved[:k]) & set(relevant) else 0.0


def _precision_at(relevant: Sequence[str], retrieved: Sequence[str], k: int) -> float:
    if k <= 0:
        return 0.0
    top = retrieved[:k]
    if not top:
        return 0.0
    return len(set(top) & set(relevant)) / len(top)


def _reciprocal_rank(relevant: Sequence[str], retrieved: Sequence[str]) -> float:
    wanted = set(relevant)
    for position, doc in enumerate(retrieved, start=1):
        if doc in wanted:
            return 1.0 / position
    return 0.0


def _ndcg_at(relevant: Sequence[str], retrieved: Sequence[str], k: int) -> float:
    """Binary-gain nDCG: every relevant document is worth 1, position-discounted.

    Graded labels would be nicer (a first-choice card is worth more than an
    also-relevant one) but the eval set has no graded labels, and inventing them
    from list order would make the metric depend on how the author happened to
    sort a JSON array.
    """

    wanted = set(relevant)
    if not wanted:
        return 0.0
    dcg = sum(
        1.0 / math.log2(position + 1)
        for position, doc in enumerate(retrieved[:k], 1)
        if doc in wanted
    )
    ideal = sorted((1.0 for _ in wanted), reverse=True)
    idcg = sum(1.0 / math.log2(position + 1) for position, _ in enumerate(ideal[:k], 1))
    return dcg / idcg if idcg > 0 else 0.0


@dataclass(frozen=True)
class RankingReport:
    """Ranking quality for one setting, with the slices the brief asks for."""

    setting: str
    n: int
    n_answerable: int
    n_no_answer: int
    n_errors: int
    #: Set-based recall, per k.
    recall: dict[int, float] = field(default_factory=dict)
    #: "At least one relevant document in the top k", per k.
    hit: dict[int, float] = field(default_factory=dict)
    precision: dict[int, float] = field(default_factory=dict)
    ndcg: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    #: Same metrics sliced by language / style / source, keyed ``lang:hi`` etc.
    slices: dict[str, dict[str, Any]] = field(default_factory=dict)
    latency_ms: dict[str, float] = field(default_factory=dict)
    #: Row-bootstrap 95% CI for the headline numbers.
    ci95: dict[str, tuple[float, float]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "setting": self.setting,
            "n": self.n,
            "n_answerable": self.n_answerable,
            "n_no_answer": self.n_no_answer,
            "n_errors": self.n_errors,
            "recall_at": {str(k): round(v, 4) for k, v in sorted(self.recall.items())},
            "hit_at": {str(k): round(v, 4) for k, v in sorted(self.hit.items())},
            "precision_at": {str(k): round(v, 4) for k, v in sorted(self.precision.items())},
            "ndcg_at": {str(k): round(v, 4) for k, v in sorted(self.ndcg.items())},
            "mrr": round(self.mrr, 4),
            "latency_ms": {k: round(v, 2) for k, v in sorted(self.latency_ms.items())},
            "ci95": {k: [round(v[0], 4), round(v[1], 4)] for k, v in sorted(self.ci95.items())},
            "slices": self.slices,
        }


def evaluate_ranking(
    results: Sequence[QueryResult],
    *,
    setting: str,
    ks: Sequence[int] = DEFAULT_KS,
    bootstrap: int = 0,
    seed: int = 20261014,
) -> RankingReport:
    """Score one retrieval setting over a split.

    ``bootstrap=0`` (the default) skips the confidence intervals; the report CLI
    passes a few thousand for the frozen test split.
    """

    answerable = [row for row in results if row.has_answer]
    latencies = [row.latency_ms for row in results]
    report = RankingReport(
        setting=setting,
        n=len(results),
        n_answerable=len(answerable),
        n_no_answer=len(results) - len(answerable),
        n_errors=sum(1 for row in results if row.error),
        recall={
            k: _mean(_recall_at(row.relevant, row.retrieved, k) for row in answerable) for k in ks
        },
        hit={k: _mean(_hit_at(row.relevant, row.retrieved, k) for row in answerable) for k in ks},
        precision={
            k: _mean(_precision_at(row.relevant, row.retrieved, k) for row in answerable)
            for k in ks
        },
        ndcg={k: _mean(_ndcg_at(row.relevant, row.retrieved, k) for row in answerable) for k in ks},
        mrr=_mean(_reciprocal_rank(row.relevant, row.retrieved) for row in answerable),
        latency_ms={
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "mean": _mean(iter(latencies)),
        },
        slices=_slices(results, ks=ks),
    )
    if bootstrap > 0 and answerable:
        report = _with_ci(report, answerable, ks=ks, bootstrap=bootstrap, seed=seed)
    return report


def _mean(values: Iterable[float]) -> float:
    collected = list(values)
    return sum(collected) / len(collected) if collected else 0.0


#: The two slice axes every table reports. Typed at module level because a
#: lambda in a loop body is an untyped callable, and mypy --strict refuses to
#: let a typed function call one.
_SLICE_KEYS: tuple[tuple[str, Callable[[QueryResult], str]], ...] = (
    ("lang", lambda row: row.lang),
    ("style", lambda row: row.style),
)


def _slices(results: Sequence[QueryResult], *, ks: Sequence[int]) -> dict[str, dict[str, Any]]:
    """Per-language and per-style slices, each with its own row count."""

    slices: dict[str, dict[str, Any]] = {}
    for prefix, key_fn in _SLICE_KEYS:
        buckets: dict[str, list[QueryResult]] = {}
        for row in results:
            if not row.has_answer:
                continue
            buckets.setdefault(key_fn(row), []).append(row)
        for name in sorted(buckets):
            rows = buckets[name]
            slices[f"{prefix}:{name}"] = {
                "n": len(rows),
                "recall_at_1": round(
                    _mean(_recall_at(r.relevant, r.retrieved, 1) for r in rows), 4
                ),
                "recall_at_3": round(
                    _mean(_recall_at(r.relevant, r.retrieved, 3) for r in rows), 4
                ),
                "recall_at_5": round(
                    _mean(_recall_at(r.relevant, r.retrieved, 5) for r in rows), 4
                ),
                "hit_at_3": round(_mean(_hit_at(r.relevant, r.retrieved, 3) for r in rows), 4),
                "mrr": round(_mean(_reciprocal_rank(r.relevant, r.retrieved) for r in rows), 4),
                "ks": list(ks),
            }
    return slices


def _with_ci(
    report: RankingReport,
    answerable: Sequence[QueryResult],
    *,
    ks: Sequence[int],
    bootstrap: int,
    seed: int,
) -> RankingReport:
    """Row-bootstrap 95% intervals for the headline metrics."""

    import random

    rng = random.Random(seed)
    rows = list(answerable)
    n = len(rows)
    samples: dict[str, list[float]] = {
        "mrr": [],
        **{f"recall_at_{k}": [] for k in ks},
        **{f"hit_at_{k}": [] for k in ks},
    }
    for _ in range(bootstrap):
        picks = [rows[rng.randrange(n)] for _ in range(n)]
        samples["mrr"].append(_mean(_reciprocal_rank(r.relevant, r.retrieved) for r in picks))
        for k in ks:
            samples[f"recall_at_{k}"].append(
                _mean(_recall_at(r.relevant, r.retrieved, k) for r in picks)
            )
            samples[f"hit_at_{k}"].append(_mean(_hit_at(r.relevant, r.retrieved, k) for r in picks))
    ci95 = {name: _interval(values) for name, values in samples.items()}
    return RankingReport(
        setting=report.setting,
        n=report.n,
        n_answerable=report.n_answerable,
        n_no_answer=report.n_no_answer,
        n_errors=report.n_errors,
        recall=report.recall,
        hit=report.hit,
        precision=report.precision,
        ndcg=report.ndcg,
        mrr=report.mrr,
        slices=report.slices,
        latency_ms=report.latency_ms,
        ci95=ci95,
    )


def _interval(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    return (percentile(values, 2.5), percentile(values, 97.5))


# --------------------------------------------------------------------- #
# Abstention: "no good answer" calibration                                #
# --------------------------------------------------------------------- #

ThresholdObjective = Literal["f1", "answer_precision", "abstain_recall"]


@dataclass(frozen=True)
class AbstainReport:
    """What an abstain threshold does to the queries that have no answer.

    Two decisions are measured, because they are the two that hurt differently:

    * **abstain precision** — of the queries the system answered *nothing* to,
      how many genuinely had nothing in the corpus. Low precision means the
      system is staying silent on questions it could have helped with.
    * **abstain recall** — of the queries that genuinely had nothing, how many
      the system stayed silent on. Low recall means irrelevant self-help cards
      are being pushed into a prompt for a question the corpus does not cover.

    ``answer_precision`` is the product-facing framing of the same table: of the
    turns that *did* get a card, how many had a relevant document in the top k.
    """

    threshold: float
    score_field: str
    n: int
    n_no_answer: int
    n_answerable: int
    abstain_precision: float
    abstain_recall: float
    abstain_f1: float
    answer_precision: float
    answer_recall: float
    #: The four cells, so a reader never has to trust the ratios.
    confusion: dict[str, int] = field(default_factory=dict)
    #: Per-language abstain recall: a threshold fitted mostly on English must
    #: not be allowed to hide a Bengali collapse.
    abstain_recall_by_lang: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "threshold": round(self.threshold, 6),
            "score_field": self.score_field,
            "n": self.n,
            "n_no_answer": self.n_no_answer,
            "n_answerable": self.n_answerable,
            "abstain_precision": round(self.abstain_precision, 4),
            "abstain_recall": round(self.abstain_recall, 4),
            "abstain_f1": round(self.abstain_f1, 4),
            "answer_precision": round(self.answer_precision, 4),
            "answer_recall": round(self.answer_recall, 4),
            "confusion": dict(self.confusion),
            "abstain_recall_by_lang": {
                key: round(value, 4) for key, value in sorted(self.abstain_recall_by_lang.items())
            },
        }


def score_abstain(
    results: Sequence[QueryResult],
    *,
    threshold: float,
    score_field: str = "top_score",
    top_k_for_answer: int = 3,
) -> AbstainReport:
    """Measure an abstain decision: answer when ``top_score >= threshold``.

    A query with no candidate at all (``top_score is None``) abstains by
    construction, which is the honest reading of a retriever that returned [].
    """

    true_abstain = 0
    false_abstain = 0
    true_answer = 0
    false_answer = 0
    answered_relevant = 0
    abstained_no_answer = 0
    by_lang: dict[str, list[tuple[bool, bool]]] = {}

    for row in results:
        abstains = row.top_score is None or row.top_score < threshold
        should_abstain = not row.has_answer
        by_lang.setdefault(row.lang, []).append((abstains, should_abstain))
        if abstains and should_abstain:
            true_abstain += 1
            abstained_no_answer += 1
        elif abstains and not should_abstain:
            false_abstain += 1
        elif not abstains and should_abstain:
            false_answer += 1
        else:
            true_answer += 1
            if set(row.retrieved[:top_k_for_answer]) & set(row.relevant):
                answered_relevant += 1

    n_abstained = true_abstain + false_abstain
    n_answered = true_answer + false_answer
    n_no_answer = true_abstain + false_answer
    abstain_precision = true_abstain / n_abstained if n_abstained else 0.0
    abstain_recall = abstained_no_answer / n_no_answer if n_no_answer else 0.0
    abstain_f1 = (
        2 * abstain_precision * abstain_recall / (abstain_precision + abstain_recall)
        if (abstain_precision + abstain_recall) > 0
        else 0.0
    )
    return AbstainReport(
        threshold=threshold,
        score_field=score_field,
        n=len(results),
        n_no_answer=n_no_answer,
        n_answerable=len(results) - n_no_answer,
        abstain_precision=abstain_precision,
        abstain_recall=abstain_recall,
        abstain_f1=abstain_f1,
        answer_precision=answered_relevant / n_answered if n_answered else 0.0,
        answer_recall=true_answer / (len(results) - n_no_answer)
        if (len(results) - n_no_answer)
        else 0.0,
        confusion={
            "true_abstain": true_abstain,
            "false_abstain": false_abstain,
            "true_answer": true_answer,
            "false_answer": false_answer,
        },
        abstain_recall_by_lang={
            lang: (
                sum(1 for abstains, should in pairs if abstains and should)
                / sum(1 for _, should in pairs if should)
                if any(should for _, should in pairs)
                else 1.0
            )
            for lang, pairs in by_lang.items()
        },
    )


def fit_abstain_threshold(
    results: Sequence[QueryResult],
    *,
    objective: ThresholdObjective = "f1",
    min_answer_precision: float = 0.0,
    candidates: Sequence[float] | None = None,
    top_k_for_answer: int = 3,
) -> AbstainReport:
    """Pick the threshold on a tuning split — never on the frozen test split.

    ``objective='f1'`` maximises the abstain decision's F1. ``'answer_precision'``
    takes the *lowest* threshold that reaches ``min_answer_precision``, which is
    the setting a product actually wants: answer as often as possible while
    keeping the cards that are shown relevant. Ties break toward the lower
    threshold, because a retriever that stays silent too often is a feature
    nobody experiences as help.
    """

    scores = sorted({row.top_score for row in results if row.top_score is not None} | {0.0})
    if candidates is None:
        # Midpoints between observed scores, plus the extremes: a threshold can
        # only ever matter where it changes a decision, so there is no point
        # searching anywhere else.
        grid = [0.0]
        grid.extend((a + b) / 2 for a, b in itertools.pairwise(scores))
        grid.append(scores[-1] + 1e-6 if scores else 1.0)
        candidates = grid

    best: AbstainReport | None = None
    for threshold in sorted(candidates):
        report = score_abstain(results, threshold=threshold, top_k_for_answer=top_k_for_answer)
        if objective == "answer_precision":
            if report.answer_precision + 1e-9 < min_answer_precision:
                continue
            if best is None or report.answer_recall > best.answer_recall + 1e-9:
                best = report
        elif objective == "abstain_recall":
            if best is None or (
                report.abstain_recall > best.abstain_recall + 1e-9
                or (
                    abs(report.abstain_recall - best.abstain_recall) < 1e-9
                    and report.threshold < best.threshold
                )
            ):
                best = report
        else:
            if best is None or (
                report.abstain_f1 > best.abstain_f1 + 1e-9
                or (
                    abs(report.abstain_f1 - best.abstain_f1) < 1e-9
                    and report.threshold < best.threshold
                )
            ):
                best = report
    if best is None:
        # Nothing met the constraint: abstain everywhere rather than pretend a
        # threshold exists. Loud in the report, honest in the numbers.
        return score_abstain(results, threshold=math.inf, top_k_for_answer=top_k_for_answer)
    return best
