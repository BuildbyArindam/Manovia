"""Tune the fusion weight on dev, calibrate the abstain floor on calibration.

This module produces the one file that turns ``RETRIEVER=hybrid`` from a
plausible default into a measured setting: ``tuning.json``, which
:func:`app.services.rag.factory.load_tuning` reads at boot. Two numbers, two
splits, and the fingerprints that prove which is which.

* **The fusion weight** (``dense_weight``) is grid-searched on the **dev** split
  only. It is the single knob that decides how much a cosine is worth against a
  BM25 score once reciprocal rank fusion has made them comparable, and it is
  exactly the kind of knob that overfits: 85 dev queries is a small sample, so
  the search is a coarse grid from the config and the whole curve is written into
  the artefact — a reader can see whether the peak is a plateau or a spike on one
  lucky query.
* **The abstain floor** (``abstain_score``) is fitted on the **calibration**
  split, separately for ``hybrid`` and ``hybrid_rerank``, because the two settings
  score on different quantities (fused evidence versus a reranker probability) and
  a threshold is only meaningful for the scale it was fitted on.

The **test** split is never read here. That is not a comment, it is a type: the
config fields this module reads are ``Literal``s whose members do not include
``test`` (``calibration.fit_split``, ``reranker.train_split``), and the split
fingerprints it writes into ``tuning.json`` let anyone check afterwards that the
file's numbers came from the splits their names claim.
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

from manovia_ml.paths import resolve
from manovia_ml.retrieval.corpus import build_passages, corpus_fingerprint, load_documents
from manovia_ml.retrieval.data import (
    RetrievalQuery,
    eval_set_ref,
    load_eval_set,
    split_fingerprint,
    split_rows,
)
from manovia_ml.retrieval.harness import (
    SETTINGS,
    Harness,
    HarnessUnavailable,
    ensure_backend,
    read_tuning,
)
from manovia_ml.retrieval.metrics import (
    QueryResult,
    ThresholdObjective,
    evaluate_ranking,
    fit_abstain_threshold,
    percentile,
)
from manovia_ml.seeds import seed_everything
from manovia_ml.tracking import tracked_run

__all__ = [
    "ABSTAIN_SETTINGS",
    "BACKEND_RELPATH",
    "THRESHOLD_OBJECTIVES",
    "CalibrationSummary",
    "FusionSummary",
    "backend_artifact_dir",
    "calibrate_abstain",
    "install_backend",
    "main",
    "sweep_fusion",
    "write_tuning",
]

#: Where the backend reads the file from, relative to the repository root. A
#: deployment can point ``RAG_RERANKER_DIR`` elsewhere; this is the packaged
#: location, and it is committed because it is calibration data a reviewer must
#: be able to read, not a weight (AGENTS.md ML ground rule 3).
BACKEND_RELPATH = "backend/app/ml_artifacts/retrieval_v1"

#: The objectives a threshold can be fitted under. The config picks one; the
#: others are measured anyway and recorded beside it, because "we chose 0.42" is
#: only meaningful next to "0.31 would have answered more and been wrong twice".
THRESHOLD_OBJECTIVES: tuple[ThresholdObjective, ...] = (
    "answer_precision",
    "f1",
    "abstain_recall",
)

#: The two settings a threshold is calibrated for. ``baseline`` is not here: the
#: Day-13 retriever keeps its own ``RAG_MIN_SIMILARITY`` floor, which was
#: calibrated on Day 13 and is not this module's to move.
ABSTAIN_SETTINGS = ("hybrid", "hybrid_rerank")


@dataclass(frozen=True)
class FusionSummary:
    """The dev sweep: the chosen weight and the curve it was chosen from."""

    split: str
    metric: str
    best_weight: float
    best_score: float
    n_queries: int
    grid: tuple[tuple[float, float], ...] = ()
    table: tuple[dict[str, Any], ...] = ()
    embedder_kind: str = "tfidf"
    fingerprints: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0
    smoke: bool = False
    run_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "metric": self.metric,
            "best_weight": self.best_weight,
            "best_score": round(self.best_score, 4),
            "n_queries": self.n_queries,
            "grid": [[weight, round(score, 4)] for weight, score in self.grid],
            "table": list(self.table),
            "embedder_kind": self.embedder_kind,
            "fingerprints": self.fingerprints,
            "seconds": round(self.seconds, 3),
            "smoke": self.smoke,
            "run_id": self.run_id,
        }


@dataclass(frozen=True)
class CalibrationSummary:
    """One abstain decision per setting, fitted on the calibration split."""

    split: str
    objective: str
    min_answer_precision: float
    top_k_for_answer: int
    thresholds: dict[str, float] = field(default_factory=dict)
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The same scores under the other two objectives. Only the configured one is
    #: shipped, but a threshold is a trade-off, and the report cannot describe the
    #: trade-off without the alternatives next to it.
    alternatives: dict[str, dict[str, Any]] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    score_spread: dict[str, dict[str, float]] = field(default_factory=dict)
    fingerprints: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0
    smoke: bool = False
    run_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "objective": self.objective,
            "min_answer_precision": self.min_answer_precision,
            "top_k_for_answer": self.top_k_for_answer,
            "thresholds": {key: round(value, 6) for key, value in self.thresholds.items()},
            "reports": self.reports,
            "alternatives": self.alternatives,
            "unavailable": self.unavailable,
            "score_spread": self.score_spread,
            "fingerprints": self.fingerprints,
            "seconds": round(self.seconds, 3),
            "smoke": self.smoke,
            "run_id": self.run_id,
        }


def _load(config: Any) -> tuple[dict[str, list[RetrievalQuery]], dict[str, str], int]:
    """The splits, the fingerprints, and the corpus the numbers belong to."""

    data = config.data
    documents = load_documents(resolve(data.corpus_dir) if data.corpus_dir.strip() else None)
    passages = build_passages(
        documents, chunk_words=data.chunk_words, overlap_words=data.chunk_overlap_words
    )
    rows = load_eval_set(resolve(data.eval_set), corpus_ids={doc.id for doc in documents})
    grouped = split_rows(rows, seed=data.split_seed)
    fingerprints = {
        "eval_set": eval_set_ref(resolve(data.eval_set)).sha256,
        "corpus": corpus_fingerprint(passages),
        "train": split_fingerprint(grouped["train"]),
        "dev": split_fingerprint(grouped["dev"]),
        "calibration": split_fingerprint(grouped["calibration"]),
        "test": split_fingerprint(grouped["test"]),
    }
    return grouped, fingerprints, len(passages)


def _harness(config: Any, workdir: Path | None, tuning: Mapping[str, Any]) -> Harness:
    encoder_dirs: dict[str, Path] = {}
    if config.encoder.artifact_dir.strip():
        encoder_dirs[config.encoder.kind] = resolve(config.encoder.artifact_dir)
    return Harness(
        config,
        workdir=workdir or resolve("data/retrieval_artifacts/harness"),
        encoder_dirs=encoder_dirs,
        reranker_dir=(
            resolve(config.reranker.artifact_dir) if config.reranker.artifact_dir.strip() else None
        ),
        tuning=dict(tuning),
    )


def sweep_fusion(
    config: Any,
    *,
    harness: Harness | None = None,
    workdir: Path | None = None,
    track: bool = True,
) -> FusionSummary | None:
    """Grid-search ``dense_weight`` on dev. ``None`` when there is no backend."""

    started = time.perf_counter()
    seed_state = seed_everything(config.seed)
    grouped, fingerprints, _ = _load(config)
    dev_rows = grouped["dev"]
    if not dev_rows:
        raise SystemExit("the dev split is empty; there is nothing to tune a fusion weight on")
    if harness is None:
        harness = _harness(config, workdir, {})

    ks = tuple(config.evaluation.ks)
    spec = next(item for item in SETTINGS if item.name == "hybrid")
    reason = harness.unavailable_reason(spec)
    if reason is not None:
        print(f"SKIP fusion sweep: {reason}")
        return None

    weights = tuple(config.hybrid.dense_weight_grid)
    table: list[dict[str, Any]] = []

    def score(results: Sequence[QueryResult]) -> float:
        report = evaluate_ranking(results, setting="dev-sweep", ks=ks)
        table.append(
            {
                "recall_at_1": round(report.recall.get(1, 0.0), 4),
                "recall_at_3": round(report.recall.get(3, 0.0), 4),
                "recall_at_5": round(report.recall.get(5, 0.0), 4),
                "mrr": round(report.mrr, 4),
                "hit_at_3": round(report.hit.get(3, 0.0), 4),
                "p95_latency_ms": round(report.latency_ms.get("p95", 0.0), 2),
            }
        )
        return float(report.mrr)

    grid = tuple(harness.sweep(dev_rows, spec, weights, limit=config.hybrid.top_k, score=score))
    # Ties break toward the *lower* dense weight: the lexical half is the one
    # that works in every language of the corpus without a multilingual
    # checkpoint, so when two weights measure the same, prefer the one that
    # depends less on the dense encoder.
    best_score = max(value for _, value in grid)
    best_weight = min(weight for weight, value in grid if value == best_score)
    summary = FusionSummary(
        split="dev",
        metric="mrr",
        best_weight=best_weight,
        best_score=best_score,
        n_queries=len(dev_rows),
        grid=grid,
        table=tuple(
            {"dense_weight": weight, **row}
            for (weight, _score), row in zip(grid, table, strict=True)
        ),
        embedder_kind=spec.encoder,
        fingerprints=fingerprints,
        seconds=time.perf_counter() - started,
        smoke=config.training.phase == "smoke",
    )
    if track:
        summary = _track_fusion(config, summary, seed_state=seed_state)
    _print_fusion(summary)
    return summary


def _track_fusion(config: Any, summary: FusionSummary, *, seed_state: Any) -> FusionSummary:
    params = {
        "hybrid.dense_weight_grid": str(list(config.hybrid.dense_weight_grid)),
        "hybrid.fusion_k": config.hybrid.fusion_k,
        "hybrid.bm25_k1": config.hybrid.bm25_k1,
        "hybrid.bm25_b": config.hybrid.bm25_b,
        "hybrid.title_boost": config.hybrid.title_boost,
        "hybrid.top_k": config.hybrid.top_k,
        "tuning.split": summary.split,
        "tuning.metric": summary.metric,
        "tuning.embedder_kind": summary.embedder_kind,
        **{f"fingerprint.{name}": value for name, value in summary.fingerprints.items()},
    }
    tags = {"manovia.smoke": "true" if summary.smoke else "false", "manovia.stage": "fusion"}
    with tracked_run(
        config,
        phase=config.training.phase,
        seed_state=seed_state,
        params=params,
        tags=tags,
    ) as run:
        run.log_metrics(
            {
                "dev/best_dense_weight": summary.best_weight,
                "dev/best_mrr": summary.best_score,
                **{f"dev_sweep/w{weight:.2f}": score for weight, score in summary.grid},
                "fusion/seconds": summary.seconds,
            }
        )
        run.log_dict(summary.as_dict(), "fusion_sweep.json")
        return dataclasses.replace(summary, run_id=run.run_id)


def _print_fusion(summary: FusionSummary) -> None:
    kind = "SMOKE (not a result)" if summary.smoke else "dev sweep"
    print(f"fusion {kind}: {len(summary.grid)} weights over {summary.n_queries} dev queries")
    for row in summary.table:
        marker = " <-" if row["dense_weight"] == summary.best_weight else ""
        print(
            f"  dense_weight={row['dense_weight']:.2f}  mrr={row['mrr']:.4f} "
            f"r@1={row['recall_at_1']:.4f} r@3={row['recall_at_3']:.4f} "
            f"r@5={row['recall_at_5']:.4f}{marker}"
        )
    print(
        f"best dense_weight={summary.best_weight:.2f} ({summary.metric}={summary.best_score:.4f})"
    )


def _spread(results: Sequence[QueryResult]) -> dict[str, float]:
    """Where the answerable and the unanswerable scores sit, side by side.

    The abstain decision is only as good as the gap between these two
    distributions, and a threshold quoted without them is a number nobody can
    judge. Printed in the report exactly as measured.
    """

    def stats(rows: Sequence[QueryResult]) -> dict[str, float]:
        scores = [row.top_score for row in rows if row.top_score is not None]
        if not scores:
            return {"n": 0.0, "min": 0.0, "median": 0.0, "max": 0.0, "p95": 0.0}
        return {
            "n": float(len(scores)),
            "min": round(min(scores), 4),
            "median": round(percentile(scores, 50.0), 4),
            "max": round(max(scores), 4),
            "p95": round(percentile(scores, 95.0), 4),
        }

    answerable = [row for row in results if row.has_answer]
    no_answer = [row for row in results if not row.has_answer]
    return {
        "answerable_min": stats(answerable)["min"],
        "answerable_median": stats(answerable)["median"],
        "answerable_max": stats(answerable)["max"],
        "no_answer_min": stats(no_answer)["min"],
        "no_answer_median": stats(no_answer)["median"],
        "no_answer_max": stats(no_answer)["max"],
        "n_answerable": float(len(answerable)),
        "n_no_answer": float(len(no_answer)),
    }


def calibrate_abstain(
    config: Any,
    *,
    harness: Harness | None = None,
    workdir: Path | None = None,
    track: bool = True,
) -> CalibrationSummary | None:
    """Fit one abstain threshold per setting, on the calibration split."""

    started = time.perf_counter()
    seed_state = seed_everything(config.seed)
    grouped, fingerprints, _ = _load(config)
    calibration = config.calibration
    rows = grouped[calibration.fit_split]
    if not rows:
        raise SystemExit(f"the {calibration.fit_split!r} split is empty; nothing to calibrate on")
    if harness is None:
        harness = _harness(config, workdir, read_tuning(_tuning_path(config)))

    thresholds: dict[str, float] = {}
    reports: dict[str, dict[str, Any]] = {}
    alternatives: dict[str, dict[str, Any]] = {}
    unavailable: dict[str, str] = {}
    spread: dict[str, dict[str, float]] = {}
    for name in ABSTAIN_SETTINGS:
        spec = next((item for item in SETTINGS if item.name == name), None)
        if spec is None:
            unavailable[name] = "no such setting in the harness"
            continue
        reason = harness.unavailable_reason(spec)
        if reason is not None:
            unavailable[name] = reason
            continue
        results = harness.run(rows, spec, limit=calibration.top_k_for_answer, repeats=1)
        fitted = fit_abstain_threshold(
            results,
            objective=calibration.objective,
            min_answer_precision=calibration.min_answer_precision,
            top_k_for_answer=calibration.top_k_for_answer,
        )
        thresholds[name] = float(fitted.threshold)
        reports[name] = fitted.as_dict()
        for other in THRESHOLD_OBJECTIVES:
            if other == calibration.objective:
                continue
            alternative = fit_abstain_threshold(
                results,
                objective=other,
                min_answer_precision=calibration.min_answer_precision,
                top_k_for_answer=calibration.top_k_for_answer,
            )
            alternatives[f"{name}:{other}"] = alternative.as_dict()
        reports[name]["latency_ms_p95"] = round(
            percentile([row.latency_ms for row in results], 95.0), 2
        )
        spread[name] = _spread(results)

    if not thresholds:
        print(
            "SKIP abstain calibration: no setting could be measured here "
            f"({'; '.join(f'{key}: {value}' for key, value in unavailable.items())})"
        )
        return None

    summary = CalibrationSummary(
        split=calibration.fit_split,
        objective=calibration.objective,
        min_answer_precision=calibration.min_answer_precision,
        top_k_for_answer=calibration.top_k_for_answer,
        thresholds=thresholds,
        reports=reports,
        alternatives=alternatives,
        unavailable=unavailable,
        score_spread=spread,
        fingerprints=fingerprints,
        seconds=time.perf_counter() - started,
        smoke=config.training.phase == "smoke",
    )
    if track:
        summary = _track_calibration(config, summary, seed_state=seed_state)
    _print_calibration(summary)
    return summary


def _track_calibration(
    config: Any, summary: CalibrationSummary, *, seed_state: Any
) -> CalibrationSummary:
    params = {
        "calibration.objective": summary.objective,
        "calibration.min_answer_precision": summary.min_answer_precision,
        "calibration.top_k_for_answer": summary.top_k_for_answer,
        "calibration.fit_split": summary.split,
        **{f"fingerprint.{name}": value for name, value in summary.fingerprints.items()},
        **{f"threshold.{name}": value for name, value in summary.thresholds.items()},
    }
    tags = {"manovia.smoke": "true" if summary.smoke else "false", "manovia.stage": "abstain"}
    metrics: dict[str, float] = {"calibration/seconds": summary.seconds}
    for name, report in summary.reports.items():
        for key in (
            "abstain_precision",
            "abstain_recall",
            "abstain_f1",
            "answer_precision",
            "answer_recall",
            "threshold",
        ):
            metrics[f"{name}/{key}"] = float(report.get(key, 0.0))
    with tracked_run(
        config,
        phase=config.training.phase,
        seed_state=seed_state,
        params=params,
        tags=tags,
    ) as run:
        run.log_metrics(metrics)
        run.log_dict(summary.as_dict(), "abstain_calibration.json")
        return dataclasses.replace(summary, run_id=run.run_id)


def _print_calibration(summary: CalibrationSummary) -> None:
    kind = "SMOKE (not a result)" if summary.smoke else "calibration"
    print(
        f"abstain {kind}: objective={summary.objective} on {len(summary.thresholds)} setting(s), "
        f"split={summary.split}"
    )
    for name, threshold in sorted(summary.thresholds.items()):
        report = summary.reports[name]
        print(
            f"  {name:<16} threshold={threshold:.4f} "
            f"abstain_precision={report['abstain_precision']:.3f} "
            f"abstain_recall={report['abstain_recall']:.3f} "
            f"answer_precision={report['answer_precision']:.3f} "
            f"answer_recall={report['answer_recall']:.3f}"
        )
        confusion = report.get("confusion", {})
        print(f"                   confusion={confusion}")
        for key, alternative in sorted(summary.alternatives.items()):
            if not key.startswith(f"{name}:"):
                continue
            print(
                f"                   (alternative {key.split(':', 1)[1]}: "
                f"threshold={alternative['threshold']:.4f} "
                f"answer_precision={alternative['answer_precision']:.3f} "
                f"answer_recall={alternative['answer_recall']:.3f} "
                f"abstain_recall={alternative['abstain_recall']:.3f})"
            )
    for name, reason in sorted(summary.unavailable.items()):
        print(f"  {name}: not calibrated here — {reason}")


def _tuning_path(config: Any) -> Path:
    """Where this config's tuning.json lives (ml side, gitignored)."""

    return resolve(f"data/retrieval_artifacts/{config.name}/tuning.json")


def write_tuning(
    path: Path,
    *,
    fusion: FusionSummary | None = None,
    calibration: CalibrationSummary | None = None,
    config: Any | None = None,
    embedder_kind: str = "",
) -> dict[str, Any]:
    """Merge the fitted numbers into ``tuning.json``, keeping what is already there.

    The file is written by two stages that run at different times (the sweep
    before the reranker exists, the calibration after), so each stage merges
    rather than replaces: running the second must not delete the first.

    Keys inside ``dev`` that start with ``_`` are provenance — the backend's
    loader ignores them by design, so the file can carry the curve it was fitted
    from without the retriever having to understand it.
    """

    payload: dict[str, Any] = {}
    if path.is_file():
        existing = read_tuning(path)
        payload.update(existing)

    hybrid = config.hybrid if config is not None else None
    dev: dict[str, Any] = dict(payload.get("dev") or {})
    if fusion is not None:
        dev.update(
            {
                "dense_weight": round(fusion.best_weight, 4),
                "_metric": fusion.metric,
                "_split": fusion.split,
                "_n_queries": fusion.n_queries,
                "_best_score": round(fusion.best_score, 4),
                "_grid": [[weight, round(score, 4)] for weight, score in fusion.grid],
                "_table": [dict(row) for row in fusion.table],
                "_fitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "_run_id": fusion.run_id,
                "_smoke": fusion.smoke,
            }
        )
        payload["embedder_kind"] = fusion.embedder_kind
    if hybrid is not None:
        # The knobs that were *held fixed* during the sweep travel too, so a
        # reader can tell what was searched from what was assumed.
        dev.setdefault("fusion_k", hybrid.fusion_k)
        dev.setdefault("bm25_k1", hybrid.bm25_k1)
        dev.setdefault("bm25_b", hybrid.bm25_b)
        dev.setdefault("title_boost", hybrid.title_boost)
        dev.setdefault("rerank_candidates", hybrid.rerank_candidates)
        dev["_dense_scale_at_fit"] = round(2.0 * 0.12, 4)
    if dev:
        payload["dev"] = dev
    if embedder_kind:
        payload["embedder_kind"] = embedder_kind

    abstain: dict[str, Any] = dict(payload.get("abstain") or {})
    if calibration is not None:
        abstain.update({name: round(value, 6) for name, value in calibration.thresholds.items()})
        abstain.update(
            {
                "_objective": calibration.objective,
                "_min_answer_precision": calibration.min_answer_precision,
                "_top_k_for_answer": calibration.top_k_for_answer,
                "_split": calibration.split,
                "_reports": calibration.reports,
                "_alternatives": calibration.alternatives,
                "_score_spread": calibration.score_spread,
                "_unavailable": calibration.unavailable,
                "_fitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "_run_id": calibration.run_id,
                "_smoke": calibration.smoke,
            }
        )
    if abstain:
        payload["abstain"] = abstain

    splits: dict[str, Any] = dict(payload.get("splits") or {})
    for summary in (fusion, calibration):
        if summary is not None:
            splits.update(summary.fingerprints)
    if config is not None:
        splits["split_seed"] = config.data.split_seed
        splits["config"] = config.name
    if splits:
        payload["splits"] = splits
    payload["_schema"] = "manovia.retrieval.tuning/v1"
    payload["_note"] = (
        "Written by ml/src/manovia_ml/retrieval/tuning.py. `dev` was fitted on the dev split, "
        "`abstain` on the calibration split; the test split fingerprints in `splits` are here so "
        "a reader can confirm the reported numbers were not tuned on. Keys starting with `_` are "
        "provenance and are ignored by app.services.rag.factory.load_tuning."
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def backend_artifact_dir(repo_root: Path | None = None) -> Path:
    """The backend's packaged retrieval artefact directory, inside the checkout.

    Separate from :func:`install_backend` because the path itself is worth a
    test: resolving it one directory too high writes ``backend/app/ml_artifacts``
    *next to* the repository instead of inside it, which looks like success and
    leaves the deployment without its calibration.
    """

    return (repo_root or resolve("..")) / BACKEND_RELPATH


def install_backend(payload: Mapping[str, Any], *, repo_root: Path | None = None) -> Path:
    """Copy ``tuning.json`` into the backend's packaged artefact directory.

    The backend must boot with the calibrated numbers on a fresh clone (and the
    numbers are the evidence that tuning happened on dev), so this file is
    committed — unlike the reranker coefficients, which are a fitted model and
    stay out of Git (AGENTS.md ML ground rule 3). ``load_cross_encoder`` falls
    back to the documented untrained weights and the factory logs the downgrade.
    """

    destination = backend_artifact_dir(repo_root)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "tuning.json"
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m manovia_ml.retrieval.tuning`` — sweep, calibrate, write."""

    from manovia_ml.config import load_retrieval_config

    parser = argparse.ArgumentParser(
        description="Tune the fusion weight on dev and calibrate the abstain floor."
    )
    parser.add_argument("--config", default="configs/retrieval_hybrid.yaml")
    parser.add_argument(
        "--stage", choices=("fusion", "abstain", "all"), default="all", help="which half to run"
    )
    parser.add_argument("--workdir", default="data/retrieval_artifacts/harness")
    parser.add_argument("--out", default="", help="tuning.json to write (default: per-config)")
    parser.add_argument(
        "--install-backend",
        action="store_true",
        help="also write backend/app/ml_artifacts/retrieval_v1/tuning.json",
    )
    parser.add_argument("--no-track", action="store_true")
    parser.add_argument(
        "--require-backend",
        action="store_true",
        help="fail instead of skipping when the backend is not importable",
    )
    args = parser.parse_args(argv)

    try:
        ensure_backend()
    except HarnessUnavailable as exc:
        if args.require_backend:
            raise SystemExit(f"backend unavailable: {exc}") from exc
        print(
            f"SKIP: the backend is not importable here ({exc}); the fusion sweep and the "
            "abstain calibration both measure the serving path, so there is nothing to fit."
        )
        return 0

    config_path = resolve(args.config)
    config = load_retrieval_config(config_path)
    out_path = resolve(args.out) if args.out else _tuning_path(config)
    existing = read_tuning(out_path)

    fusion: FusionSummary | None = None
    calibration: CalibrationSummary | None = None
    if args.stage in {"fusion", "all"}:
        fusion = sweep_fusion(config, workdir=resolve(args.workdir), track=not args.no_track)
    if args.stage in {"abstain", "all"}:
        # The calibration reads the file the sweep just wrote, so a threshold is
        # fitted at the weight that will actually be served.
        harness = _harness(
            config,
            resolve(args.workdir),
            {**existing, **({"dev": {"dense_weight": fusion.best_weight}} if fusion else {})},
        )
        calibration = calibrate_abstain(
            config, harness=harness, workdir=resolve(args.workdir), track=not args.no_track
        )
    if fusion is None and calibration is None:
        print("nothing was fitted; tuning.json left as it was")
        return 0

    payload = write_tuning(out_path, fusion=fusion, calibration=calibration, config=config)
    print(f"wrote {out_path}")
    if args.install_backend:
        installed = install_backend(payload)
        print(f"installed {installed}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
