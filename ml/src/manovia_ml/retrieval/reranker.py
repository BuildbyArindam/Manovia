"""Fit the cross-encoder stage: coefficients over the first stage's own output.

The reranker in ``app/services/rag/rerank.py`` is a linear scorer over twelve
features (``FEATURE_NAMES``): the two first-stage scores, five lexical-overlap
features, script/tag agreement and query length. Shipping it *untrained* means
serving hand-set weights, which is a guess with a docstring. This module
replaces the guess with a fit — a distilled cross-encoder in the sense the brief
allows: the labels come from the human-reviewed eval set, the features come from
the serving path, and the model is small enough to review coefficient by
coefficient.

Three rules the code enforces rather than the author remembering:

* **The features are the serving path's features.** Rows are built by calling
  :meth:`app.services.rag.hybrid.HybridKnowledgeRetriever.candidates` and
  ``cross_encoder_features`` — the same functions a chat turn runs, with the same
  corpus IDF. A fitter that rebuilt the feature rows would drift from serving and
  nothing downstream would notice except worse numbers.
* **It fits on ``train`` and reports on ``dev``.** ``reranker.train_split`` is a
  ``Literal["train", "dev"]``; there is no ``test`` member, so the split a number
  is quoted from cannot be the split the coefficients were fitted on.
* **The artefact carries its own contract.** ``feature_version`` and
  ``bm25_saturation_pivot`` travel in the metadata because
  ``load_cross_encoder`` refuses to serve coefficients fitted against a different
  feature layout or a different saturation pivot — silently rescaled weights are
  the failure mode a JSON metadata file exists to prevent.

A transformer cross-encoder (``reranker.kind: transformer``) is not fitted here:
it needs torch and a checkpoint, and
``ml/notebooks/colab_retrieval_finetune.ipynb`` runs it on the same labelled
pairs. This module writes those pairs out (``reranker_examples.jsonl``) so the
two paths train on identical data.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from manovia_ml.hashing import dataset_ref
from manovia_ml.paths import resolve
from manovia_ml.retrieval.corpus import build_passages, corpus_fingerprint, load_documents
from manovia_ml.retrieval.data import (
    eval_set_ref,
    load_eval_set,
    split_fingerprint,
    split_rows,
)
from manovia_ml.retrieval.harness import (
    SETTINGS,
    Harness,
    HarnessUnavailable,
    SettingSpec,
    ensure_backend,
)
from manovia_ml.seeds import derive_seed, seed_everything
from manovia_ml.tracking import tracked_run

__all__ = [
    "EXAMPLES_FILE",
    "RerankerExample",
    "RerankerSummary",
    "collect_examples",
    "fit_coefficients",
    "fit_reranker",
    "install_backend",
    "main",
    "save_artifact",
    "score_examples",
]

#: The labelled (query, candidate) rows, written next to the artefact so the
#: Colab notebook trains a transformer cross-encoder on the same data.
EXAMPLES_FILE = "reranker_examples.jsonl"

#: The first-stage row the examples are collected from: hybrid fusion, no
#: reranker (a reranker cannot score its own training candidates), the encoder
#: production serves offline.
FITTING_SPEC = SettingSpec(
    name="reranker_fit",
    retriever="hybrid",
    encoder="tfidf",
    reranker="none",
    label="first stage the cross-encoder is fitted on",
)


@dataclass(frozen=True)
class RerankerExample:
    """One labelled row: a candidate the first stage produced, for one query."""

    query_id: str
    query: str
    lang: str
    doc_id: str
    title: str
    features: tuple[float, ...]
    label: int
    #: Position in the fused order (1-based) — the reranker's job is to move
    #: relevant documents up, so the starting position is part of the record.
    fused_rank: int
    evidence: float


@dataclass(frozen=True)
class RerankerSummary:
    """What the fit produced, as plain data."""

    artifact_dir: str
    fitted_on: str
    reported_on: str
    rows: int
    positives: int
    coefficients: dict[str, float] = field(default_factory=dict)
    fitted: dict[str, float] = field(default_factory=dict)
    held_out: dict[str, float] = field(default_factory=dict)
    untrained_held_out: dict[str, float] = field(default_factory=dict)
    gain: dict[str, float] = field(default_factory=dict)
    fingerprints: dict[str, str] = field(default_factory=dict)
    feature_version: int = 1
    bm25_saturation_pivot: float = 5.0
    c: float = 1.0
    seconds: float = 0.0
    smoke: bool = False
    run_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_dir": self.artifact_dir,
            "fitted_on": self.fitted_on,
            "reported_on": self.reported_on,
            "rows": self.rows,
            "positives": self.positives,
            "coefficients": {k: round(v, 6) for k, v in self.coefficients.items()},
            "fitted": self.fitted,
            "held_out": self.held_out,
            "untrained_held_out": self.untrained_held_out,
            "gain": self.gain,
            "fingerprints": self.fingerprints,
            "feature_version": self.feature_version,
            "bm25_saturation_pivot": self.bm25_saturation_pivot,
            "c": self.c,
            "seconds": round(self.seconds, 3),
            "smoke": self.smoke,
            "run_id": self.run_id,
        }


def collect_examples(
    harness: Harness,
    rows: Sequence[Any],
    *,
    spec: SettingSpec = FITTING_SPEC,
    limit: int = 5,
) -> list[RerankerExample]:
    """Labelled feature rows, from the serving path's own candidates.

    Every candidate the first stage produced is a row, relevant or not: a
    reranker fitted only on the documents a labeller liked never learns what an
    irrelevant near-miss looks like, which is the only thing it is for.
    """

    from app.services.rag.rerank import cross_encoder_features

    retriever = harness.build(spec)
    idf = harness.idf(spec)
    examples: list[RerankerExample] = []
    for row in rows:
        candidates, evidence = retriever.candidates(row.query, limit=limit)
        relevant = set(row.relevant_docs)
        for position, (candidate, score) in enumerate(zip(candidates, evidence, strict=True), 1):
            features = cross_encoder_features(row.query, candidate, idf=idf)
            examples.append(
                RerankerExample(
                    query_id=row.id,
                    query=row.query,
                    lang=row.lang,
                    doc_id=candidate.doc_id,
                    title=candidate.title,
                    features=tuple(float(value) for value in features),
                    label=1 if candidate.doc_id in relevant else 0,
                    fused_rank=position,
                    evidence=float(score),
                )
            )
    return examples


def _matrix(examples: Sequence[RerankerExample]) -> tuple[np.ndarray, np.ndarray]:
    """Feature matrix (without the bias column) and labels."""

    from app.services.rag.rerank import FEATURE_NAMES

    bias_index = FEATURE_NAMES.index("bias")
    rows: list[list[float]] = []
    for example in examples:
        rows.append([value for index, value in enumerate(example.features) if index != bias_index])
    matrix: np.ndarray = np.asarray(rows, dtype=np.float64) if rows else np.zeros((0, 11))
    labels: np.ndarray = np.asarray([example.label for example in examples], dtype=np.int64)
    return matrix, labels


def fit_coefficients(
    examples: Sequence[RerankerExample], *, c: float = 1.0, seed: int = 20261014
) -> tuple[float, ...]:
    """Balanced logistic regression over the eleven real features.

    ``class_weight="balanced"`` because the rows are dominated by irrelevant
    candidates (a 20-candidate window over a 40-document corpus has at most a
    handful of relevant ones), and an unbalanced fit learns "say no", which is
    the untrained behaviour we are trying to replace. The intercept goes in the
    ``bias`` slot so the artefact is a single vector in ``FEATURE_NAMES`` order —
    the shape :func:`app.services.rag.rerank.LexicalCrossEncoder` expects.
    """

    from app.services.rag.rerank import FEATURE_NAMES
    from sklearn.linear_model import LogisticRegression

    matrix, labels = _matrix(examples)
    if matrix.shape[0] == 0:
        raise ValueError("no reranker examples to fit")
    if len(set(labels.tolist())) < 2:
        raise ValueError(
            "every candidate carries the same label; a reranker fitted on this "
            "would learn a constant"
        )
    model = LogisticRegression(
        C=c,
        class_weight="balanced",
        max_iter=5000,
        random_state=derive_seed(seed, "reranker"),
    )
    model.fit(matrix, labels)
    coefficients = [float(model.intercept_[0]), *(float(value) for value in model.coef_[0])]
    if len(coefficients) != len(FEATURE_NAMES):
        raise ValueError(
            f"fitted {len(coefficients)} coefficients, the build expects {len(FEATURE_NAMES)}"
        )
    return tuple(coefficients)


def _logit(features: Sequence[float], coefficients: Sequence[float]) -> float:
    return float(sum(weight * value for weight, value in zip(coefficients, features, strict=True)))


def score_examples(
    examples: Sequence[RerankerExample], coefficients: Sequence[float]
) -> dict[str, float]:
    """How good a coefficient vector is, on rows it may or may not have seen.

    Three numbers, because one is never enough:

    * ``roc_auc`` — separates relevant from irrelevant at all (threshold-free,
      so it does not reward a lucky cut).
    * ``relevant_first`` — of the queries with at least one relevant candidate,
      the share where the *top-scoring* candidate is relevant. This is the
      product-facing question: does the card the person sees help?
    * ``mrr`` — mean reciprocal rank of the first relevant candidate under the
      new order, which is what moves Recall@1.
    """

    from sklearn.metrics import roc_auc_score

    labels = [example.label for example in examples]
    scores = [_logit(example.features, coefficients) for example in examples]
    out: dict[str, float] = {}
    out["roc_auc"] = float(roc_auc_score(labels, scores)) if len(set(labels)) > 1 else 0.0

    by_query: dict[str, list[tuple[float, int]]] = {}
    for example, score in zip(examples, scores, strict=True):
        by_query.setdefault(example.query_id, []).append((score, example.label))
    answerable = {key: value for key, value in by_query.items() if any(label for _, label in value)}
    if answerable:
        first_hit = 0
        reciprocal = 0.0
        for pairs in answerable.values():
            ordered = sorted(pairs, key=lambda item: (-item[0],))
            if ordered[0][1] == 1:
                first_hit += 1
            for rank, (_, label) in enumerate(ordered, 1):
                if label == 1:
                    reciprocal += 1.0 / rank
                    break
        out["relevant_first"] = first_hit / len(answerable)
        out["mrr"] = reciprocal / len(answerable)
        out["queries"] = float(len(answerable))
    else:
        out["relevant_first"] = 0.0
        out["mrr"] = 0.0
        out["queries"] = 0.0
    return {key: round(value, 4) for key, value in out.items()}


def save_artifact(directory: Path, coefficients: Sequence[float], summary: RerankerSummary) -> None:
    """Write the artefact in the layout ``load_cross_encoder`` reads."""

    from app.services.rag.rerank import (
        BM25_SATURATION_PIVOT,
        FEATURE_NAMES,
        FEATURE_VERSION,
        RERANKER_METADATA_FILE,
        RERANKER_WEIGHTS_FILE,
    )

    directory.mkdir(parents=True, exist_ok=True)
    (directory / RERANKER_WEIGHTS_FILE).write_text(
        json.dumps(
            {
                "features": list(FEATURE_NAMES),
                "weights": [round(float(value), 6) for value in coefficients],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (directory / RERANKER_METADATA_FILE).write_text(
        json.dumps(
            {
                "kind": "lexical",
                "trained": True,
                "feature_version": FEATURE_VERSION,
                "bm25_saturation_pivot": BM25_SATURATION_PIVOT,
                "algorithm": "balanced logistic regression over cross_encoder_features",
                "fitted_on_split": summary.fitted_on,
                "reported_on_split": summary.reported_on,
                "rows": summary.rows,
                "positives": summary.positives,
                "logistic_C": summary.c,
                "held_out": summary.held_out,
                "untrained_held_out": summary.untrained_held_out,
                "fingerprints": summary.fingerprints,
                "run_id": summary.run_id,
                "smoke": summary.smoke,
                "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def install_backend(artifact_dir: Path, *, repo_root: Path | None = None) -> list[Path]:
    """Copy the fitted coefficients beside the committed calibration file.

    The backend loads ``reranker_weights.json`` and ``reranker.json`` from
    ``backend/app/ml_artifacts/retrieval_v1/`` — its packaged artefact directory,
    the same one ``retrieval/tuning.py`` writes ``tuning.json`` to. The two files
    here are *weights*: they are gitignored and regenerated by this module, while
    ``tuning.json`` beside them is calibration data and is committed.

    Without this step ``RETRIEVER=hybrid_rerank`` logs ``rag_retriever_downgraded``
    and serves plain hybrid. That is the right fallback and the wrong outcome to
    discover by accident: the deployment quietly loses the day's main result.
    """

    from app.services.rag.rerank import RERANKER_METADATA_FILE, RERANKER_WEIGHTS_FILE

    from manovia_ml.retrieval.tuning import backend_artifact_dir

    destination = backend_artifact_dir(repo_root)
    destination.mkdir(parents=True, exist_ok=True)
    installed: list[Path] = []
    for name in (RERANKER_WEIGHTS_FILE, RERANKER_METADATA_FILE):
        source = artifact_dir / name
        if not source.is_file():
            raise FileNotFoundError(f"no {name} in {artifact_dir} (did the fit run?)")
        target = destination / name
        target.write_bytes(source.read_bytes())
        installed.append(target)
    return installed


def _write_examples(path: Path, examples: Sequence[RerankerExample]) -> None:
    from app.services.rag.rerank import FEATURE_NAMES

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "query_id": example.query_id,
                "query": example.query,
                "lang": example.lang,
                "doc_id": example.doc_id,
                "title": example.title,
                "label": example.label,
                "fused_rank": example.fused_rank,
                "evidence": round(example.evidence, 6),
                "features": dict(zip(FEATURE_NAMES, example.features, strict=True)),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for example in examples
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def fit_reranker(
    config: Any,
    *,
    harness: Harness | None = None,
    workdir: Path | None = None,
    tuning: Any | None = None,
    track: bool = True,
) -> RerankerSummary | None:
    """Fit, score, save. ``None`` when the backend is not importable here."""

    from app.services.rag.rerank import (
        BM25_SATURATION_PIVOT,
        DEFAULT_WEIGHTS,
        FEATURE_VERSION,
        load_cross_encoder,
    )

    started = time.perf_counter()
    seed_state = seed_everything(config.seed)
    data = config.data
    documents = load_documents(resolve(data.corpus_dir) if data.corpus_dir.strip() else None)
    passages = build_passages(
        documents, chunk_words=data.chunk_words, overlap_words=data.chunk_overlap_words
    )
    rows = load_eval_set(resolve(data.eval_set), corpus_ids={doc.id for doc in documents})
    grouped = split_rows(rows, seed=data.split_seed)
    fit_split = config.reranker.train_split
    fit_rows = grouped[fit_split]
    report_split = "dev" if fit_split == "train" else "train"
    report_rows = grouped[report_split]
    if not fit_rows:
        raise SystemExit(f"the {fit_split!r} split is empty; nothing to fit a reranker on")

    if harness is None:
        directory = workdir or resolve("data/retrieval_artifacts/harness")
        harness = Harness(
            config,
            workdir=directory,
            encoder_dirs={},
            reranker_dir=None,
            tuning=tuning if isinstance(tuning, dict) else None,
        )

    examples = collect_examples(harness, fit_rows, limit=config.evaluation.ks[-1])
    held_out_examples = collect_examples(harness, report_rows, limit=config.evaluation.ks[-1])
    coefficients = fit_coefficients(examples, c=config.reranker.c, seed=config.seed)
    fitted = score_examples(examples, coefficients)
    held_out = score_examples(held_out_examples, coefficients)
    untrained = score_examples(held_out_examples, DEFAULT_WEIGHTS)
    gain = {
        key: round(held_out[key] - untrained[key], 4)
        for key in held_out
        if key in untrained and key != "queries"
    }

    artifact_dir = resolve(config.reranker.artifact_dir or "data/retrieval_artifacts/reranker_v1")
    fingerprints = {
        "eval_set": eval_set_ref(resolve(data.eval_set)).sha256,
        "corpus": corpus_fingerprint(passages),
        "train": split_fingerprint(grouped["train"]),
        "dev": split_fingerprint(grouped["dev"]),
        "calibration": split_fingerprint(grouped["calibration"]),
        "test": split_fingerprint(grouped["test"]),
    }
    summary = RerankerSummary(
        artifact_dir=str(artifact_dir),
        fitted_on=fit_split,
        reported_on=report_split,
        rows=len(examples),
        positives=sum(example.label for example in examples),
        coefficients=dict(
            zip(
                _feature_names(),
                (round(float(value), 6) for value in coefficients),
                strict=True,
            )
        ),
        fitted=fitted,
        held_out=held_out,
        untrained_held_out=untrained,
        gain=gain,
        fingerprints=fingerprints,
        feature_version=FEATURE_VERSION,
        bm25_saturation_pivot=BM25_SATURATION_PIVOT,
        c=config.reranker.c,
        seconds=time.perf_counter() - started,
        smoke=config.training.phase == "smoke",
    )
    save_artifact(artifact_dir, coefficients, summary)
    _write_examples(artifact_dir / EXAMPLES_FILE, [*examples, *held_out_examples])

    # Round-trip through the backend's own loader: an artefact this module can
    # write but serving cannot read is worse than no artefact at all.
    loaded = load_cross_encoder(artifact_dir, idf=harness.idf(FITTING_SPEC))
    assert list(getattr(loaded, "weights", ())) == [
        round(float(value), 6) for value in coefficients
    ], "the artefact did not round-trip through load_cross_encoder"

    if track:
        summary = _track(config, summary, seed_state=seed_state, artifact_dir=artifact_dir)
    _print(summary)
    return summary


def _feature_names() -> tuple[str, ...]:
    from app.services.rag.rerank import FEATURE_NAMES

    return tuple(FEATURE_NAMES)


def _track(
    config: Any, summary: RerankerSummary, *, seed_state: Any, artifact_dir: Path
) -> RerankerSummary:
    params = {
        "reranker.kind": config.reranker.kind,
        "reranker.c": config.reranker.c,
        "reranker.candidates": config.reranker.candidates,
        "reranker.train_split": config.reranker.train_split,
        "rows": summary.rows,
        "positives": summary.positives,
        "feature_version": summary.feature_version,
        **{f"fingerprint.{name}": value for name, value in summary.fingerprints.items()},
        **{f"coefficient.{name}": value for name, value in summary.coefficients.items()},
    }
    tags = {
        "manovia.smoke": "true" if summary.smoke else "false",
        "manovia.stage": "reranker",
    }
    examples_path = artifact_dir / EXAMPLES_FILE
    with tracked_run(
        config,
        phase=config.training.phase,
        data_refs=(
            [
                dataset_ref(
                    examples_path,
                    name="reranker-examples",
                    text_field="query",
                    label_field="label",
                )
            ]
            if examples_path.is_file()
            else []
        ),
        seed_state=seed_state,
        params=params,
        tags=tags,
    ) as run:
        run.log_metrics(
            {
                **{f"reranker_fit/{key}": value for key, value in summary.fitted.items()},
                **{f"reranker_held_out/{key}": value for key, value in summary.held_out.items()},
                **{
                    f"reranker_untrained/{key}": value
                    for key, value in summary.untrained_held_out.items()
                },
                **{f"reranker_gain/{key}": value for key, value in summary.gain.items()},
                "reranker/seconds": summary.seconds,
            }
        )
        run.log_dict(summary.as_dict(), "reranker_summary.json")
        for name in ("reranker_weights.json", "reranker.json"):
            path = artifact_dir / name
            if path.is_file():
                run.log_artifact(path, "reranker")
        return dataclasses.replace(summary, run_id=run.run_id)


def _print(summary: RerankerSummary) -> None:
    kind = "SMOKE (not a result)" if summary.smoke else "fit"
    print(
        f"reranker {kind}: {summary.rows} labelled candidates "
        f"({summary.positives} relevant) from the {summary.fitted_on} split"
    )
    print(f"  on {summary.fitted_on}: {summary.fitted}")
    print(
        f"  on {summary.reported_on}: fitted {summary.held_out} vs untrained "
        f"{summary.untrained_held_out} -> gain {summary.gain}"
    )
    print("  coefficients:")
    for name, value in summary.coefficients.items():
        print(f"    {name:<22} {value:+.4f}")
    print(f"wrote {summary.artifact_dir} in {summary.seconds:.1f}s")
    if summary.run_id:
        print(f"mlflow run {summary.run_id}")


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m manovia_ml.retrieval.reranker`` — fit the cross-encoder."""

    from manovia_ml.config import load_retrieval_config
    from manovia_ml.retrieval.harness import read_tuning

    parser = argparse.ArgumentParser(description="Fit the lexical cross-encoder coefficients.")
    parser.add_argument("--config", default="configs/retrieval_hybrid.yaml")
    parser.add_argument("--workdir", default="data/retrieval_artifacts/harness")
    parser.add_argument(
        "--tuning",
        default="",
        help="tuning.json to read the fitted fusion knobs from (default: the packaged one)",
    )
    parser.add_argument("--no-track", action="store_true")
    parser.add_argument(
        "--install-backend",
        action="store_true",
        help="copy the fitted coefficients into backend/app/ml_artifacts/retrieval_v1/",
    )
    parser.add_argument(
        "--require-backend",
        action="store_true",
        help="fail instead of skipping when the backend is not importable",
    )
    args = parser.parse_args(argv)

    if config_kind_is_transformer(args.config):
        raise SystemExit(
            "reranker.kind is 'transformer'; a cross-encoder checkpoint is trained by "
            "ml/notebooks/colab_retrieval_finetune.ipynb. This entry point fits the "
            "offline lexical coefficients."
        )
    try:
        ensure_backend()
    except HarnessUnavailable as exc:
        if args.require_backend:
            raise SystemExit(f"backend unavailable: {exc}") from exc
        print(
            f"SKIP: the backend is not importable here, so there is no first stage to "
            f"collect candidates from ({exc})."
        )
        return 0

    config_path = resolve(args.config)
    config = load_retrieval_config(config_path)
    tuning = read_tuning(args.tuning) if args.tuning else {}
    summary = fit_reranker(
        config, workdir=resolve(args.workdir), tuning=tuning, track=not args.no_track
    )
    if summary is None:
        return 1
    if args.install_backend:
        for path in install_backend(resolve(summary.artifact_dir)):
            print(f"installed {path} (gitignored: fitted weights are never committed)")
    return 0


def config_kind_is_transformer(config_path: str) -> bool:
    """Peek at ``reranker.kind`` without loading the whole config twice."""

    from manovia_ml.config import load_retrieval_config

    try:
        return load_retrieval_config(resolve(config_path)).reranker.kind == "transformer"
    except Exception:  # a bad config is the loader's problem, reported below
        return False


#: Kept for callers that want the rows the eight report settings use.
SETTING_NAMES = tuple(spec.name for spec in SETTINGS)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
