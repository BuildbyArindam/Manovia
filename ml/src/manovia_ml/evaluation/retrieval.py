"""Evaluate every retrieval setting on one split, and write the report from it.

This is the module behind ``make ml-retrieval-eval``: it runs the eight settings
in :data:`manovia_ml.retrieval.harness.SETTINGS` over the split the config names
(``test`` for the numbers the Day-35 report quotes), scores each with
:func:`manovia_ml.retrieval.metrics.evaluate_ranking`, applies the abstain
thresholds that ``retrieval/tuning.py`` fitted on the *calibration* split, and
renders ``ml/reports/day35-retrieval.md`` from what it measured.

Four things this module refuses to do, because each would produce a report that
looks right and is not:

* **It never fits anything.** No weight, no threshold, no epoch is chosen here.
  Everything it applies was fitted by an earlier stage on a named split, and the
  split fingerprints it prints come from the artefacts those stages wrote, so a
  reader can check the test split never entered.
* **It never drops a row it could not measure.** A setting whose encoder or
  reranker artefact is missing appears in the table with its reason, not absent.
* **It never invents a cause for a failure.** Every failure case carries the
  evidence for its label (what was retrieved, at what score, in what language,
  with how much lexical overlap), and the cause is computed from that evidence.
* **It never quotes a smoke run as a result.** ``training.phase: smoke`` travels
  into the title, the MLflow tags and this report's first line.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from manovia_ml.paths import reports_dir, resolve
from manovia_ml.retrieval.corpus import (
    Passage,
    build_passages,
    corpus_fingerprint,
    load_documents,
)
from manovia_ml.retrieval.data import (
    RetrievalQuery,
    eval_set_ref,
    load_eval_set,
    split_fingerprint,
    split_rows,
)
from manovia_ml.retrieval.harness import SETTINGS, Harness, ensure_backend, read_tuning
from manovia_ml.retrieval.metrics import (
    QueryResult,
    RankingReport,
    evaluate_ranking,
    score_abstain,
)
from manovia_ml.seeds import seed_everything
from manovia_ml.tracking import tracked_run

__all__ = [
    "EvaluationSummary",
    "FailureCase",
    "SettingResult",
    "classify_failure",
    "evaluate_settings",
    "main",
    "pick_recommendation",
    "render_report",
    "write_report",
]

#: The rows the brief asks to compare, in the order the report prints them. The
#: others are measured too (they are what makes a recommendation checkable) but
#: the headline table is these.
HEADLINE = ("baseline", "dense_finetuned", "hybrid", "hybrid_rerank")

#: Settings whose score scale a calibrated abstain threshold was fitted for.
#: Every other row is a measurement, not a deployment setting, and says so.
ABSTAIN_SETTINGS = ("hybrid", "hybrid_rerank")


@dataclass(frozen=True)
class FailureCase:
    """One query the best setting still gets wrong, with the evidence for why."""

    query_id: str
    query: str
    lang: str
    style: str
    relevant: tuple[str, ...]
    retrieved: tuple[str, ...]
    top_score: float | None
    threshold: float | None
    cause: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "query": self.query,
            "lang": self.lang,
            "style": self.style,
            "relevant": list(self.relevant),
            "retrieved": list(self.retrieved),
            "top_score": None if self.top_score is None else round(self.top_score, 4),
            "threshold": None if self.threshold is None else round(self.threshold, 4),
            "cause": self.cause,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class SettingResult:
    """Everything measured for one row of the comparison table."""

    name: str
    label: str
    status: str  # "measured" | "unavailable"
    reason: str = ""
    report: RankingReport | None = None
    abstain: Any | None = None
    threshold: float | None = None
    threshold_source: str = ""
    results: tuple[QueryResult, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "label": self.label,
            "status": self.status,
            "reason": self.reason,
            "threshold": self.threshold,
            "threshold_source": self.threshold_source,
        }
        if self.report is not None:
            payload["ranking"] = self.report.as_dict()
        if self.abstain is not None:
            payload["abstain"] = self.abstain.as_dict()
        return payload


@dataclass(frozen=True)
class EvaluationSummary:
    """The whole evaluation, as plain data (JSON sidecar + MLflow artefact)."""

    split: str
    n_rows: int
    ks: tuple[int, ...]
    settings: tuple[SettingResult, ...] = ()
    recommended: str = ""
    recommendation: str = ""
    failures: tuple[FailureCase, ...] = ()
    splits: dict[str, Any] = field(default_factory=dict)
    tuning: dict[str, Any] = field(default_factory=dict)
    corpus: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    smoke: bool = False
    run_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "n_rows": self.n_rows,
            "ks": list(self.ks),
            "settings": [item.as_dict() for item in self.settings],
            "recommended": self.recommended,
            "recommendation": self.recommendation,
            "failures": [item.as_dict() for item in self.failures],
            "splits": self.splits,
            "tuning": self.tuning,
            "corpus": self.corpus,
            "seconds": round(self.seconds, 3),
            "smoke": self.smoke,
            "run_id": self.run_id,
        }

    def by_name(self, name: str) -> SettingResult | None:
        return next((item for item in self.settings if item.name == name), None)


# ------------------------------------------------------------------- measuring #


def evaluate_settings(
    config: Any,
    *,
    harness: Harness,
    rows: Sequence[RetrievalQuery],
    tuning: Mapping[str, Any],
) -> tuple[SettingResult, ...]:
    """Run every setting over ``rows`` and score what came back."""

    ks = tuple(config.evaluation.ks)
    raw_abstain = tuning.get("abstain")
    abstain_thresholds: Mapping[str, Any] = (
        dict(raw_abstain) if isinstance(raw_abstain, dict) else {}
    )
    out: list[SettingResult] = []
    for spec in SETTINGS:
        reason = harness.unavailable_reason(spec)
        if reason is not None:
            out.append(SettingResult(spec.name, spec.describe(), "unavailable", reason=reason))
            continue
        results = harness.run(rows, spec, limit=max(ks), repeats=config.evaluation.latency_repeats)
        report = evaluate_ranking(
            results,
            setting=spec.name,
            ks=ks,
            bootstrap=config.evaluation.bootstrap,
            seed=config.seed,
        )
        threshold: float | None = None
        abstain = None
        source = ""
        if spec.name in ABSTAIN_SETTINGS:
            raw = abstain_thresholds.get(spec.name)
            if isinstance(raw, (int, float)):
                threshold = float(raw)
                source = f"tuning.json (fitted on the {config.calibration.fit_split} split)"
                abstain = score_abstain(
                    results,
                    threshold=threshold,
                    top_k_for_answer=config.calibration.top_k_for_answer,
                )
        out.append(
            SettingResult(
                spec.name,
                spec.describe(),
                "measured",
                report=report,
                abstain=abstain,
                threshold=threshold,
                threshold_source=source,
                results=tuple(results),
            )
        )
    return tuple(out)


# --------------------------------------------------------------------- failures #


def _tokens(text: str) -> set[str]:
    return {token for token in text.casefold().replace(",", " ").split() if len(token) > 2}


def classify_failure(
    row: QueryResult,
    *,
    query: str,
    passages: Sequence[Passage],
    threshold: float | None,
) -> tuple[str, dict[str, Any]]:
    """Why this row failed, from evidence rather than from a guess.

    The causes are the ones a reader can act on, and each is checkable from the
    numbers returned beside it:

    * ``abstain-miss`` — a no-answer query the system answered.
    * ``abstain-overreach`` — an answerable query the system stayed silent on.
    * ``ranking`` — the right document was retrieved, just not first.
    * ``cross-lingual`` — the answer exists in another language than the query.
    * ``vocabulary`` — no lexical bridge at all between query and answer.
    * ``cross-lingual`` — no labelled document is in the query's own language.
    * ``missed-relevant`` — the document is there and shares words with the query,
      and neither half of the first stage retrieved it: a recall failure.
    """

    by_doc: dict[str, list[Passage]] = {}
    for passage in passages:
        by_doc.setdefault(passage.doc_id, []).append(passage)

    if not row.has_answer:
        answered = threshold is None or (row.top_score is not None and row.top_score >= threshold)
        if answered:
            return "abstain-miss", {
                "top_score": None if row.top_score is None else round(row.top_score, 4),
                "threshold": threshold,
                "margin_over_threshold": (
                    None
                    if row.top_score is None or threshold is None
                    else round(row.top_score - threshold, 4)
                ),
                "note": "no document in the corpus answers this query, and one was returned",
            }
        return "correct-refusal", {}

    if not row.retrieved:
        return "no-candidates", {"note": "both halves of the first stage returned nothing"}

    relevant = set(row.relevant)
    hit_positions = {doc: row.retrieved.index(doc) + 1 for doc in relevant if doc in row.retrieved}
    if hit_positions:
        best = min(hit_positions.values())
        if best == 1:
            return "not-a-failure", {"first_relevant_rank": 1}
        return "ranking", {
            "first_relevant_rank": best,
            "retrieved": list(row.retrieved),
            "note": "the right document was retrieved but not placed first",
        }

    query_tokens = _tokens(query)
    evidence: dict[str, Any] = {"retrieved": list(row.retrieved), "relevant": sorted(relevant)}
    overlaps: dict[str, int] = {}
    row_language = row.lang.split("-", 1)[0]
    same_language = False
    for doc_id in sorted(relevant):
        text_tokens: set[str] = set()
        languages: set[str] = set()
        for passage in by_doc.get(doc_id, []):
            text_tokens |= _tokens(passage.embed_text)
            languages.add(passage.language)
        overlaps[doc_id] = len(query_tokens & text_tokens)
        if row_language in languages:
            same_language = True
    evidence["shared_content_tokens"] = overlaps
    evidence["answer_available_in_query_language"] = same_language
    evidence["top_score"] = None if row.top_score is None else round(row.top_score, 4)
    if max(overlaps.values(), default=0) == 0:
        # Cross-lingual only when *no* labelled document is in the query's own
        # language: a Bengali query whose Bengali answer was still missed is a
        # recall failure, not a translation gap, and the two have different fixes
        # (a wider candidate pool versus a multilingual checkpoint).
        cause = "vocabulary" if same_language else "cross-lingual"
        evidence["note"] = "the query shares no content token with any labelled document" + (
            ", though one of them is in the query's language"
            if same_language
            else ", and none of them is in the query's language"
        )
        return cause, evidence
    evidence["note"] = (
        "the query shares vocabulary with a labelled document that neither half of the first "
        "stage retrieved: a recall failure, not a missing document"
    )
    return "missed-relevant", evidence


def collect_failures(
    result: SettingResult,
    *,
    passages: Sequence[Passage],
    rows: Sequence[RetrievalQuery],
    limit: int,
) -> tuple[FailureCase, ...]:
    """The worst ``limit`` rows for one setting, each with a computed cause."""

    if result.report is None:
        return ()
    text_of = {row.id: row.query for row in rows}
    cases: list[FailureCase] = []
    for row in result.results:
        cause, evidence = classify_failure(
            row,
            query=text_of.get(row.query_id, ""),
            passages=passages,
            threshold=result.threshold,
        )
        if cause in {"not-a-failure", "correct-refusal"}:
            continue
        cases.append(
            FailureCase(
                query_id=row.query_id,
                query=text_of.get(row.query_id, ""),
                lang=row.lang,
                style=row.style,
                relevant=row.relevant,
                retrieved=row.retrieved,
                top_score=row.top_score,
                threshold=result.threshold,
                cause=cause,
                evidence=evidence,
            )
        )
    # Worst first: a no-answer query that was answered, then a miss with no
    # lexical bridge, then a mere ordering problem.
    order = {
        "abstain-miss": 0,
        "no-candidates": 1,
        "missed-relevant": 2,
        "vocabulary": 3,
        "cross-lingual": 4,
        "abstain-overreach": 5,
        "ranking": 6,
    }
    cases.sort(key=lambda case: (order.get(case.cause, 9), case.query_id))
    return tuple(cases[:limit])


# ---------------------------------------------------------------- recommendation #


def pick_recommendation(
    settings: Sequence[SettingResult], *, min_answer_precision: float
) -> tuple[str, str]:
    """Which row to serve, and the measured reason.

    Two rules, both from the numbers:

    1. A setting whose abstain decision cannot hold ``min_answer_precision`` is
       not recommendable, however good its ranking is — pushing an irrelevant
       self-help card into a prompt is the failure this product cannot afford.
    2. Of what remains, the highest MRR wins, ties broken on Recall@1 and then on
       p95 latency, because a reranker that buys nothing measurable is a cost.

    When nothing clears rule 1 the recommendation says so instead of picking the
    least bad option and dressing it up.
    """

    measured = [item for item in settings if item.status == "measured" and item.report is not None]
    if not measured:
        return "", "nothing could be measured in this environment"

    def key(item: SettingResult) -> tuple[float, float, float]:
        assert item.report is not None
        latency = float(item.report.latency_ms.get("p95", 0.0))
        return (item.report.mrr, item.report.recall.get(1, 0.0), -latency)

    eligible: list[SettingResult] = []
    for item in measured:
        if item.name not in ABSTAIN_SETTINGS:
            # A row with no calibrated threshold is a measurement, not a setting
            # anybody can serve: recommending it would recommend an uncalibrated
            # abstain decision.
            continue
        precision = float(item.abstain.answer_precision) if item.abstain is not None else 0.0
        if precision + 1e-9 >= min_answer_precision:
            eligible.append(item)
    pool = eligible or [item for item in measured if item.name in ABSTAIN_SETTINGS] or measured
    best = max(pool, key=key)
    assert best.report is not None
    baseline = next((item for item in measured if item.name == "baseline"), None)
    parts = [
        f"{best.name}: MRR {best.report.mrr:.3f}, Recall@1 "
        f"{best.report.recall.get(1, 0.0):.3f}, Recall@3 {best.report.recall.get(3, 0.0):.3f}, "
        f"Recall@5 {best.report.recall.get(5, 0.0):.3f}, "
        f"p95 {best.report.latency_ms.get('p95', 0.0):.1f} ms"
    ]
    if baseline is not None and baseline.report is not None:
        parts.append(
            f"vs the Day-13 baseline at MRR {baseline.report.mrr:.3f} / Recall@1 "
            f"{baseline.report.recall.get(1, 0.0):.3f} / p95 "
            f"{baseline.report.latency_ms.get('p95', 0.0):.1f} ms"
        )
    if best.abstain is not None:
        parts.append(
            f"abstain precision {best.abstain.answer_precision:.3f} "
            f"(cards shown that were relevant), abstain recall "
            f"{best.abstain.abstain_recall:.3f} at threshold {_fmt(best.threshold, 4)}"
        )
    if not eligible:
        parts.append(
            f"NOTE: no setting reached min_answer_precision={min_answer_precision:.2f} on this "
            "split, so this is the best available rather than a setting that clears the bar"
        )
    # A row can measure better and still not be recommendable, and hiding that
    # would make the recommendation look stronger than it is.
    better = [
        item
        for item in measured
        if item.report is not None
        and item.name != best.name
        and item.report.mrr > best.report.mrr + 1e-9
    ]
    for item in sorted(better, key=lambda entry: -(entry.report.mrr if entry.report else 0.0)):
        assert item.report is not None
        why = (
            "no calibrated abstain threshold (it is a measurement, not a deployment setting)"
            if item.name not in ABSTAIN_SETTINGS
            else "it did not hold the answer-precision floor"
        )
        parts.append(
            f"`{item.name}` measured higher (MRR {item.report.mrr:.3f}) but is not "
            f"recommendable: {why}"
        )
    return best.name, "; ".join(parts)


# ----------------------------------------------------------------------- report #


def _short(digest: Any) -> str:
    """A fingerprint shortened for a table cell (the full one is in the JSON)."""

    text = str(digest)
    return f"{text[:16]}…" if len(text) > 16 else text


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join([" --- "] * len(header)) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def render_report(summary: EvaluationSummary, *, config_name: str) -> str:
    """The markdown report, built only from measured numbers."""

    ks = summary.ks
    title_suffix = " (SMOKE — plumbing, not a result)" if summary.smoke else ""
    lines = [
        f"# Day 35 retrieval report — {config_name}{title_suffix}",
        "",
        f"Generated by `manovia_ml.evaluation.retrieval` from measured runs on the "
        f"**{summary.split}** split ({summary.n_rows} queries, k={list(ks)}). "
        "Nothing here is fitted: the fusion weight came from the dev sweep and the abstain "
        "thresholds from the calibration split, both by `manovia_ml.retrieval.tuning`.",
        "",
        f"Recommended setting: **`RETRIEVER={summary.recommended or 'none'}`** — "
        f"{summary.recommendation or 'no setting could be measured here'}.",
        "",
        "## Headline comparison",
        "",
    ]

    header = ["setting", "what it is"]
    header += [f"R@{k}" for k in ks]
    header += ["MRR", "hit@3", "p50 ms", "p95 ms", "n"]
    rows: list[list[str]] = []
    for name in HEADLINE:
        item = summary.by_name(name)
        if item is None:
            continue
        rows.append(_row_for(item, ks))
    for item in summary.settings:
        if item.name in HEADLINE:
            continue
        rows.append(_row_for(item, ks))
    lines.append(_table(header, [list(row) for row in rows]))
    lines.append("")

    measured = [item for item in summary.settings if item.status == "measured"]
    headline = [item for item in measured if item.name in HEADLINE] or measured
    if headline and headline[0].report is not None and headline[0].report.ci95:
        ci_rows: list[list[str]] = []
        for item in headline:
            report = item.report
            assert report is not None
            for metric, bounds in sorted(report.ci95.items()):
                ci_rows.append(
                    [
                        item.name,
                        metric,
                        _fmt(bounds[0]),
                        _fmt(_point(item, metric)),
                        _fmt(bounds[1]),
                    ]
                )
        lines += [
            "Row-bootstrap 95% confidence intervals for the four headline settings (the split "
            "is small; a difference inside its own interval is not a finding):",
            "",
            _table(["setting", "metric", "low", "point", "high"], ci_rows),
            "",
        ]

    lines += _delta_section(summary, ks)
    lines += ["## Per language", ""]
    languages = sorted(
        {
            key.split(":", 1)[1]
            for item in measured
            if item.report is not None
            for key in item.report.slices
            if key.startswith("lang:")
        }
    )
    for language in languages:
        rows = []
        for name in HEADLINE:
            item = summary.by_name(name)
            if item is None or item.report is None:
                continue
            slice_metrics = item.report.slices.get(f"lang:{language}", {})
            rows.append(
                [
                    name,
                    _fmt(slice_metrics.get("n", 0), 0),
                    *[_fmt(slice_metrics.get(f"recall_at_{k}")) for k in ks],
                    _fmt(slice_metrics.get("mrr")),
                    _fmt(slice_metrics.get("hit_at_3")),
                ]
            )
        lines += [
            f"### `{language}`",
            "",
            _table(["setting", "n", *[f"R@{k}" for k in ks], "MRR", "hit@3"], rows),
            "",
        ]

    lines += ['## "No good answer": the abstain decision', ""]
    abstain_rows = []
    for item in summary.settings:
        if item.abstain is None or item.status != "measured":
            continue
        report = item.abstain
        confusion = report.confusion
        abstain_rows.append(
            [
                item.name,
                _fmt(item.threshold, 4),
                _fmt(report.abstain_precision),
                _fmt(report.abstain_recall),
                _fmt(report.abstain_f1),
                _fmt(report.answer_precision),
                _fmt(report.answer_recall),
                str(confusion.get("true_abstain", 0)),
                str(confusion.get("false_abstain", 0)),
                str(confusion.get("true_answer", 0)),
                str(confusion.get("false_answer", 0)),
            ]
        )
    lines.append(
        _table(
            [
                "setting",
                "threshold",
                "abstain P",
                "abstain R",
                "abstain F1",
                "answer P",
                "answer R",
                "true abstain",
                "false abstain",
                "true answer",
                "false answer",
            ],
            abstain_rows,
        )
    )
    lines += [
        "",
        "*abstain precision* — of the queries the system said nothing to, how many genuinely had "
        "nothing in the corpus. *answer precision* — of the turns that got a card, how many had a "
        "relevant document in the top 3. *false abstain* is the expensive cell: an answerable "
        "question met with silence.",
        "",
    ]
    for item in summary.settings:
        if item.status == "measured" and item.name not in ABSTAIN_SETTINGS:
            lines.append(
                f"- `{item.name}` has no calibrated threshold: it is measured for comparison, "
                "not offered as a deployment setting."
            )
    lines.append("")

    lines += ["## What was tuned, on which split", ""]
    raw_dev = summary.tuning.get("dev")
    dev: dict[str, Any] = dict(raw_dev) if isinstance(raw_dev, dict) else {}
    raw_abstain = summary.tuning.get("abstain")
    abstain_block: dict[str, Any] = dict(raw_abstain) if isinstance(raw_abstain, dict) else {}
    lines.append(
        _table(
            ["number", "value", "fitted on", "fingerprint of that split"],
            [
                [
                    "dense_weight (fusion)",
                    _fmt(dev.get("dense_weight"), 4),
                    "dev",
                    str(summary.splits.get("dev", "—"))[:16] + "…",
                ],
                *[
                    [
                        f"abstain_score[{name}]",
                        _fmt(value, 4),
                        str(abstain_block.get("_split", "calibration")),
                        _short(
                            summary.splits.get(str(abstain_block.get("_split", "calibration")), "—")
                        ),
                    ]
                    for name, value in sorted(abstain_block.items())
                    if not name.startswith("_")
                ],
                [
                    "reported metrics",
                    f"split={summary.split}",
                    "nothing (frozen)",
                    str(summary.splits.get(summary.split, "—"))[:16] + "…",
                ],
            ],
        )
    )
    lines += ["", "All four split fingerprints, so the claim above is checkable:", ""]
    lines.append(
        _table(
            ["split", "rows", "sha256 of (id, query, labels)"],
            [[name, str(count), str(digest)] for name, count, digest in _split_table(summary)],
        )
    )
    lines.append("")
    raw_grid = dev.get("_grid")
    grid: list[Any] = list(raw_grid) if isinstance(raw_grid, list) else []
    if grid:
        lines += [
            "The dev fusion sweep in full (MRR per weight). A peak that is one query wide is not "
            "a finding; the report prints the curve so a reader can see the shape.",
            "",
            _table(
                ["dense_weight", "dev MRR"],
                [[_fmt(weight, 2), _fmt(score, 4)] for weight, score in grid],
            ),
            "",
        ]

    if summary.failures:
        lines += [
            f"## Failure cases ({len(summary.failures)}) — "
            f"`RETRIEVER={summary.recommended or 'best measured'}`",
            "",
            _table(
                ["#", "query", "lang", "style", "labelled", "retrieved", "score", "cause"],
                [
                    [
                        str(index),
                        case.query.replace("|", "\\|"),
                        case.lang,
                        case.style,
                        ", ".join(case.relevant) or "—",
                        ", ".join(case.retrieved) or "—",
                        _fmt(case.top_score, 4),
                        case.cause,
                    ]
                    for index, case in enumerate(summary.failures, 1)
                ],
            ),
            "",
        ]
        for index, case in enumerate(summary.failures, 1):
            lines.append(f"{index}. **{case.query_id}** — “{case.query}” → `{case.cause}`")
            lines.append(f"   - evidence: `{json.dumps(case.evidence, ensure_ascii=False)}`")
        lines.append("")

    unavailable = [item for item in summary.settings if item.status != "measured"]
    lines += ["## What could not be measured here", ""]
    if unavailable:
        lines.append(
            _table(
                ["setting", "why"],
                [[item.name, item.reason.replace("|", "\\|")] for item in unavailable],
            )
        )
    else:
        lines.append("Every setting in the harness was measurable in this environment.")
    lines += [
        "",
        "- No torch, transformers or sentence-transformers in this sandbox (Hugging Face is "
        "unreachable), so no multilingual *checkpoint* row: the fine-tuned dense row is the "
        "offline contrastive projection, and the cross-encoder row is the distilled lexical "
        "scorer. `ml/notebooks/colab_retrieval_finetune.ipynb` runs the same loss on a real "
        "checkpoint and on a real cross-encoder, on the pairs and examples these stages wrote.",
        "- Which also means the `*_finetuned` rows are **not servable today**: the backend's "
        "embedder factory builds `tfidf`, `hashing` or `sentence_transformers`, not an ml "
        "projection matrix. They measure what contrastive training does to this corpus in the "
        "only space this sandbox can train in, and they are the ablation a checkpoint run is "
        "compared against. What ships is `RETRIEVER=hybrid_rerank` with `RAG_EMBEDDER=tfidf`, "
        "which is why the recommendation is chosen among the settings that have both a "
        "calibrated abstain threshold and a servable encoder.",
        "- Which also means the `*_finetuned` rows are **not servable today**: the backend's "
        "embedder factory builds `tfidf`, `hashing` or `sentence_transformers`, not an ml "
        "projection matrix. They measure what contrastive training does to this corpus in the "
        "space this sandbox can train in, and they are the ablation the checkpoint run is "
        "compared against. What ships is `RETRIEVER=hybrid_rerank` with `RAG_EMBEDDER=tfidf`, "
        "which is why the recommendation is chosen from the settings that have a calibrated "
        "abstain threshold and a servable encoder.",
        "- Latency is measured in-process on one CPU, `repeats="
        f"{summary.corpus.get('latency_repeats', 'n/a')}` per query, p95 over "
        f"{summary.n_rows} queries. It ranks the settings against each other; it is not a "
        "production latency budget.",
        f"- Corpus: {summary.corpus.get('documents', '—')} documents, "
        f"{summary.corpus.get('passages', '—')} passages "
        f"(fingerprint `{str(summary.corpus.get('fingerprint', '—'))[:16]}…`).",
        "",
    ]
    if summary.run_id:
        lines += [f"MLflow run: `{summary.run_id}`.", ""]
    return "\n".join(lines)


def _delta_section(summary: EvaluationSummary, ks: Sequence[int]) -> list[str]:
    """What each stage costs and buys, as differences between measured rows.

    The brief asks for the reranker's latency cost and gain; a table of absolute
    numbers leaves the reader doing the subtraction, and the subtraction is the
    finding.
    """

    comparisons = (
        ("hybrid", "hybrid_rerank", "adding the fitted cross-encoder"),
        ("hybrid_rerank_untrained", "hybrid_rerank", "fitting it instead of hand-setting it"),
        ("bm25", "hybrid", "adding the dense half to BM25"),
        ("baseline", "hybrid", "adding BM25 + rank fusion to the Day-13 retriever"),
        ("baseline", "hybrid_rerank", "the whole Day-35 stack"),
        ("hybrid", "hybrid_finetuned", "the contrastive encoder (offline, not servable)"),
    )
    rows: list[list[str]] = []
    for before_name, after_name, what in comparisons:
        before = summary.by_name(before_name)
        after = summary.by_name(after_name)
        if (
            before is None
            or after is None
            or before.report is None
            or after.report is None
            or before.status != "measured"
            or after.status != "measured"
        ):
            continue
        left, right = before.report, after.report
        rows.append(
            [
                f"`{before_name}` → `{after_name}`",
                what,
                *[_signed(right.recall.get(k, 0.0) - left.recall.get(k, 0.0)) for k in ks],
                _signed(right.mrr - left.mrr),
                _signed(right.latency_ms.get("p50", 0.0) - left.latency_ms.get("p50", 0.0), 1),
                _signed(right.latency_ms.get("p95", 0.0) - left.latency_ms.get("p95", 0.0), 1),
            ]
        )
    if not rows:
        return []
    return [
        "## What each stage costs and buys",
        "",
        "Differences between measured rows over the same queries: a stage is only worth its "
        "latency if a quality column next to it moved.",
        "",
        _table(
            ["change", "what it is", *[f"ΔR@{k}" for k in ks], "ΔMRR", "Δp50 ms", "Δp95 ms"],
            rows,
        ),
        "",
    ]


def _signed(value: float, digits: int = 3) -> str:
    return f"{value:+.{digits}f}"


def _row_for(item: SettingResult, ks: Sequence[int]) -> list[str]:
    if item.status != "measured" or item.report is None:
        return [f"`{item.name}`", item.label + f" — **not measured**: {item.reason}"] + ["—"] * (
            len(ks) + 5
        )
    report = item.report
    return [
        f"`{item.name}`",
        item.label,
        *[_fmt(report.recall.get(k)) for k in ks],
        _fmt(report.mrr),
        _fmt(report.hit.get(3)),
        _fmt(report.latency_ms.get("p50"), 1),
        _fmt(report.latency_ms.get("p95"), 1),
        str(report.n),
    ]


def _point(item: SettingResult, metric: str) -> float | None:
    if item.report is None:
        return None
    if metric == "mrr":
        return item.report.mrr
    if metric.startswith("recall_at_"):
        return item.report.recall.get(int(metric.split("_")[-1]))
    if metric.startswith("hit_at_"):
        return item.report.hit.get(int(metric.split("_")[-1]))
    return None


def _split_table(summary: EvaluationSummary) -> list[tuple[str, Any, str]]:
    raw_counts = summary.splits.get("counts")
    counts: dict[str, Any] = dict(raw_counts) if isinstance(raw_counts, dict) else {}
    rows: list[tuple[str, Any, str]] = []
    for name in ("train", "dev", "calibration", "test"):
        digest = summary.splits.get(name, "—")
        rows.append((name, counts.get(name, "—"), str(digest)))
    return rows


def write_report(summary: EvaluationSummary, path: Path, *, config_name: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(summary, config_name=config_name), encoding="utf-8")
    return path


# -------------------------------------------------------------------------- CLI #


def run_evaluation(
    config: Any,
    *,
    config_path: Path | None = None,
    workdir: Path | None = None,
    harness: Harness | None = None,
    report_path: Path | None = None,
    tuning_path: Path | None = None,
    track: bool = True,
) -> EvaluationSummary | None:
    """Evaluate every setting and write the report. ``None`` with no backend."""

    try:
        ensure_backend()
    except Exception as exc:  # HarnessUnavailable, or an import error inside it
        print(f"SKIP evaluation: the backend is not importable here ({exc}).")
        return None

    started = time.perf_counter()
    seed_state = seed_everything(config.seed)
    data = config.data
    documents = load_documents(resolve(data.corpus_dir) if data.corpus_dir.strip() else None)
    passages = build_passages(
        documents, chunk_words=data.chunk_words, overlap_words=data.chunk_overlap_words
    )
    rows = load_eval_set(resolve(data.eval_set), corpus_ids={doc.id for doc in documents})
    grouped = split_rows(rows, seed=data.split_seed)
    split = config.evaluation.split
    eval_rows = grouped[split]
    if not eval_rows:
        raise SystemExit(f"the {split!r} split is empty; there is nothing to report")

    tuning_file = tuning_path or resolve(f"data/retrieval_artifacts/{config.name}/tuning.json")
    packaged = resolve("../backend/app/ml_artifacts/retrieval_v1/tuning.json")
    tuning = read_tuning(tuning_file) or (read_tuning(packaged) if packaged.is_file() else {})

    if harness is None:
        encoder_dirs: dict[str, Path] = {}
        if config.encoder.artifact_dir.strip():
            encoder_dirs[config.encoder.kind] = resolve(config.encoder.artifact_dir)
        harness = Harness(
            config,
            workdir=workdir or resolve("data/retrieval_artifacts/harness"),
            encoder_dirs=encoder_dirs,
            reranker_dir=(
                resolve(config.reranker.artifact_dir)
                if config.reranker.artifact_dir.strip()
                else None
            ),
            tuning=tuning,
        )

    settings = evaluate_settings(config, harness=harness, rows=eval_rows, tuning=tuning)
    recommended, reason = pick_recommendation(
        settings, min_answer_precision=config.calibration.min_answer_precision
    )
    best = next((item for item in settings if item.name == recommended), None)
    failures = (
        collect_failures(
            best, passages=passages, rows=eval_rows, limit=config.evaluation.failure_cases
        )
        if best is not None
        else ()
    )

    summary = EvaluationSummary(
        split=split,
        n_rows=len(eval_rows),
        ks=tuple(config.evaluation.ks),
        settings=settings,
        recommended=recommended,
        recommendation=reason,
        failures=failures,
        splits={
            "train": split_fingerprint(grouped["train"]),
            "dev": split_fingerprint(grouped["dev"]),
            "calibration": split_fingerprint(grouped["calibration"]),
            "test": split_fingerprint(grouped["test"]),
            "eval_set": eval_set_ref(resolve(data.eval_set)).sha256,
            "counts": {name: len(value) for name, value in grouped.items()},
            "from_tuning_file": dict(tuning.get("splits") or {}),
        },
        tuning=dict(tuning),
        corpus={
            "documents": len(documents),
            "passages": len(passages),
            "fingerprint": corpus_fingerprint(passages),
            "latency_repeats": config.evaluation.latency_repeats,
        },
        seconds=time.perf_counter() - started,
        smoke=config.training.phase == "smoke",
    )

    # Reports are committed (they are the deliverable); the machine-readable
    # sidecar is not, so it lives with the other run artefacts under the
    # gitignored data/ tree, next to the encoder and the tuning file it quotes.
    if report_path is not None:
        destination = report_path
    elif summary.smoke:
        destination = reports_dir() / "smoke" / f"{time.strftime('%Y-%m-%d')}-retrieval-smoke.md"
    else:
        destination = reports_dir() / "day35-retrieval.md"
    write_report(summary, destination, config_name=config.name)
    sidecar = resolve(f"data/retrieval_artifacts/{config.name}") / "evaluation.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        json.dumps(summary.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {destination}")
    print(f"wrote {sidecar}")

    if track:
        summary = _track(
            config, summary, seed_state=seed_state, report=destination, config_path=config_path
        )
    _print(summary)
    return summary


def _track(
    config: Any,
    summary: EvaluationSummary,
    *,
    seed_state: Any,
    report: Path,
    config_path: Path | None,
) -> EvaluationSummary:
    metrics: dict[str, float] = {
        "eval/seconds": summary.seconds,
        "eval/rows": float(summary.n_rows),
    }
    for item in summary.settings:
        if item.report is None:
            continue
        metrics[f"{item.name}/mrr"] = float(item.report.mrr)
        for k, value in item.report.recall.items():
            metrics[f"{item.name}/recall_at_{k}"] = float(value)
        metrics[f"{item.name}/p95_latency_ms"] = float(item.report.latency_ms.get("p95", 0.0))
        if item.abstain is not None:
            metrics[f"{item.name}/abstain_precision"] = float(item.abstain.abstain_precision)
            metrics[f"{item.name}/abstain_recall"] = float(item.abstain.abstain_recall)
            metrics[f"{item.name}/answer_precision"] = float(item.abstain.answer_precision)
    params = {
        "evaluation.split": summary.split,
        "evaluation.ks": str(list(summary.ks)),
        "evaluation.bootstrap": config.evaluation.bootstrap,
        "evaluation.latency_repeats": config.evaluation.latency_repeats,
        "evaluation.failure_cases": config.evaluation.failure_cases,
        "recommended": summary.recommended,
        **{
            f"fingerprint.{name}": value
            for name, value in summary.splits.items()
            if isinstance(value, str)
        },
    }
    tags = {
        "manovia.smoke": "true" if summary.smoke else "false",
        "manovia.stage": "retrieval-eval",
        "manovia.split": summary.split,
    }
    with tracked_run(
        config,
        phase=config.training.phase,
        seed_state=seed_state,
        params=params,
        tags=tags,
        config_path=config_path,
    ) as run:
        run.log_metrics(metrics)
        run.log_dict(summary.as_dict(), "retrieval_evaluation.json")
        run.log_artifact(report, "report")
        return dataclasses.replace(summary, run_id=run.run_id)


def _print(summary: EvaluationSummary) -> None:
    print(f"evaluated {len(summary.settings)} settings on {summary.n_rows} {summary.split} rows")
    for item in summary.settings:
        if item.report is None:
            print(f"  {item.name:<26} not measured: {item.reason}")
            continue
        report = item.report
        print(
            f"  {item.name:<26} mrr={report.mrr:.3f} "
            f"r@1={report.recall.get(1, 0.0):.3f} r@3={report.recall.get(3, 0.0):.3f} "
            f"r@5={report.recall.get(5, 0.0):.3f} "
            f"p95={report.latency_ms.get('p95', 0.0):.1f}ms"
        )
    print(f"recommended: RETRIEVER={summary.recommended or 'none'}")
    print(f"  {summary.recommendation}")


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m manovia_ml.evaluation.retrieval`` — measure and report."""

    from manovia_ml.config import load_retrieval_config

    parser = argparse.ArgumentParser(
        description="Evaluate every retrieval setting on one split and write the report."
    )
    parser.add_argument("--config", default="configs/retrieval_eval.yaml")
    parser.add_argument("--split", default="", help="override evaluation.split")
    parser.add_argument("--report", default="", help="markdown report path")
    parser.add_argument("--tuning", default="", help="tuning.json to apply")
    parser.add_argument("--workdir", default="data/retrieval_artifacts/harness")
    parser.add_argument("--no-track", action="store_true")
    args = parser.parse_args(argv)

    config_path = resolve(args.config)
    config = load_retrieval_config(config_path)
    if args.split:
        config = config.model_copy(
            update={"evaluation": config.evaluation.model_copy(update={"split": args.split})}
        )
    summary = run_evaluation(
        config,
        config_path=config_path,
        workdir=resolve(args.workdir),
        report_path=resolve(args.report) if args.report else None,
        tuning_path=resolve(args.tuning) if args.tuning else None,
        track=not args.no_track,
    )
    return 0 if summary is not None else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
