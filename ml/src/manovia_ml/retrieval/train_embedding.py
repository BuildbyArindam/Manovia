"""Contrastive fine-tuning of the retrieval encoder — the runnable half.

:mod:`manovia_ml.retrieval.contrastive` holds the loss and the optimiser; this
module is the *run*: it reads the config, cuts the splits, mines the pairs,
trains, selects an epoch on dev, writes the artefact and logs everything to
MLflow. It is the entry point behind ``make ml-retrieval-finetune``.

Three decisions here are load-bearing for the numbers the Day-35 report quotes:

* **The feature space is fitted on the passages *and* the training queries.**
  A query-only token that is out of vocabulary encodes as a zero vector, and a
  zero vector has no direction to contrast — the pair would contribute noise
  while looking like data.
* **Epoch selection reads dev, through a callback.** ``train_projection`` knows
  nothing about splits; the callback this module builds closes over the dev rows
  only, and the config key that names it (``training.select_on``) is a
  ``Literal`` with no ``test`` member. The frozen split stays frozen by
  construction rather than by discipline.
* **The baseline is measured in the same feature space.** The off-the-shelf
  comparison is ``TfidfEncoder`` over the *same* ``LexicalFeatures``: that
  isolates what training did from what the features are. Comparing against a
  different vectoriser would confound the two, and the report would be quoting
  a difference nobody could attribute.

The checkpoint path (``encoder.kind: sentence_transformers``) is not run here —
it needs torch and a download. ``ml/notebooks/colab_retrieval_finetune.ipynb``
runs the same loss on the same pairs on a real multilingual model; this module
writes the pairs file that notebook reads, so the two paths cannot drift.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from manovia_ml.hashing import DatasetRef, dataset_ref
from manovia_ml.paths import resolve
from manovia_ml.retrieval.contrastive import EpochRecord, train_projection
from manovia_ml.retrieval.corpus import Passage, build_passages, corpus_fingerprint, load_documents
from manovia_ml.retrieval.data import (
    RetrievalQuery,
    eval_set_ref,
    load_eval_set,
    split_fingerprint,
    split_rows,
)
from manovia_ml.retrieval.embeddings import (
    LexicalFeatures,
    ProjectionEncoder,
    TfidfEncoder,
)
from manovia_ml.retrieval.harness import encoder_artifact_dir, rank_documents
from manovia_ml.retrieval.metrics import QueryResult, RankingReport, evaluate_ranking
from manovia_ml.retrieval.pairs import MiningReport, TrainingPair, build_pairs
from manovia_ml.seeds import derive_seed, seed_everything
from manovia_ml.tracking import tracked_run

__all__ = ["PAIRS_FILE", "TrainingSummary", "dev_selection_scorer", "main", "train_encoder"]

#: Where the training pairs are written for the Colab notebook to pick up. A
#: checkpoint fine-tune must train on *these* pairs — same queries, same
#: positives, same mined negatives — or the two encoders in the report were not
#: trained on the same thing and the comparison means nothing.
PAIRS_FILE = "training_pairs.jsonl"


@dataclass(frozen=True)
class TrainingSummary:
    """What a fine-tuning run did, as plain data (JSON-able, loggable)."""

    artifact_dir: str
    encoder_name: str
    dim: int
    feature_dim: int
    pairs: int
    hard_negatives: int
    tagged_negatives: int
    pairs_without_negatives: int
    best_epoch: int | None
    select_on: str
    baseline: dict[str, float]
    trained: dict[str, float]
    delta: dict[str, float]
    history: tuple[dict[str, Any], ...]
    fingerprints: dict[str, str]
    corpus_fingerprint: str
    seconds: float
    smoke: bool
    run_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_dir": self.artifact_dir,
            "encoder_name": self.encoder_name,
            "dim": self.dim,
            "feature_dim": self.feature_dim,
            "pairs": self.pairs,
            "hard_negatives": self.hard_negatives,
            "tagged_negatives": self.tagged_negatives,
            "pairs_without_negatives": self.pairs_without_negatives,
            "best_epoch": self.best_epoch,
            "select_on": self.select_on,
            "baseline": self.baseline,
            "trained": self.trained,
            "delta": self.delta,
            "history": list(self.history),
            "fingerprints": self.fingerprints,
            "corpus_fingerprint": self.corpus_fingerprint,
            "seconds": round(self.seconds, 3),
            "smoke": self.smoke,
            "run_id": self.run_id,
        }


def _rank_all(
    encoder: Any, rows: Sequence[RetrievalQuery], passages: Sequence[Passage], *, limit: int
) -> list[QueryResult]:
    """Score every row with a backend-free dense ranking (no latency measured).

    Latency is a property of the serving path, so it belongs to the harness; the
    selection metric only needs the order.
    """

    results: list[QueryResult] = []
    for row in rows:
        retrieved = rank_documents(encoder, row.query, passages, limit=limit)
        results.append(
            QueryResult(
                query_id=row.id,
                lang=row.lang,
                style=row.style,
                relevant=tuple(row.relevant_docs),
                retrieved=retrieved,
                top_score=None,
                latency_ms=0.0,
            )
        )
    return results


def dev_selection_scorer(
    dev_rows: Sequence[RetrievalQuery],
    passages: Sequence[Passage],
    features: LexicalFeatures,
    *,
    select_on: str,
    ks: Sequence[int],
) -> Callable[[np.ndarray], float]:
    """The epoch-selection callback: candidate weights in, one dev number out.

    ``select_on`` is validated by the config's ``Literal``, so the only branch
    that can reach here is a dev metric; ``last_epoch`` passes ``None`` to the
    trainer instead of building a scorer at all.
    """

    limit = max(ks)

    def score(weights: np.ndarray) -> float:
        encoder = ProjectionEncoder(features, weights)
        report: RankingReport = evaluate_ranking(
            _rank_all(encoder, dev_rows, passages, limit=limit), setting="dev-selection"
        )
        if select_on == "dev_recall_at_3":
            return float(report.recall.get(3, 0.0))
        return float(report.mrr)

    return score


def _headline(report: RankingReport, ks: Sequence[int]) -> dict[str, float]:
    metrics: dict[str, float] = {"mrr": float(report.mrr)}
    for k in ks:
        metrics[f"recall_at_{k}"] = float(report.recall.get(k, 0.0))
    return metrics


def train_encoder(
    config: Any,
    *,
    config_path: Path | None = None,
    track: bool = True,
) -> TrainingSummary:
    """Train (or smoke-train) the projection encoder a config names.

    ``track=False`` runs the same code without MLflow, which is what the tests
    use: the artefact, the summary and every number are produced either way, so
    an untracked run is not a different run.
    """

    started = time.perf_counter()
    seed_state = seed_everything(config.seed)
    data = config.data
    training = config.training
    ks = tuple(config.evaluation.ks)

    corpus_dir = resolve(data.corpus_dir) if data.corpus_dir.strip() else None
    documents = load_documents(corpus_dir)
    passages = build_passages(
        documents, chunk_words=data.chunk_words, overlap_words=data.chunk_overlap_words
    )
    eval_set_path = resolve(data.eval_set)
    rows = load_eval_set(eval_set_path, corpus_ids={doc.id for doc in documents})
    grouped = split_rows(rows, seed=data.split_seed)
    train_rows = grouped["train"]
    dev_rows = grouped["dev"]
    if not train_rows or not dev_rows:
        raise SystemExit(
            f"the eval set at {eval_set_path} produced an empty train or dev split "
            f"(train={len(train_rows)}, dev={len(dev_rows)})"
        )

    # Passages first, then the training queries: a query-only token must not be
    # out of vocabulary, or its pair encodes as a zero vector.
    features = LexicalFeatures.fit(
        [passage.embed_text for passage in passages] + [row.query for row in train_rows],
        config.encoder.features,
    )
    baseline_encoder = TfidfEncoder(features)
    pairs, mining = build_pairs(
        train_rows,
        passages,
        encoder=baseline_encoder,
        negatives=training.hard_negatives,
        candidates=training.mine_candidates,
        max_positives_per_query=training.max_positives_per_query,
    )

    scorer = (
        None
        if training.select_on == "last_epoch"
        else dev_selection_scorer(dev_rows, passages, features, select_on=training.select_on, ks=ks)
    )
    result = train_projection(
        pairs,
        passages,
        features=features,
        dim=config.encoder.dim,
        epochs=training.epochs,
        batch_size=training.batch_size,
        lr=training.lr,
        scale=training.scale,
        seed=derive_seed(config.seed, "projection"),
        select_score=scorer,
    )

    trained_encoder = ProjectionEncoder(features, result.weights)
    baseline_report = evaluate_ranking(
        _rank_all(baseline_encoder, dev_rows, passages, limit=max(ks)), setting="dev-baseline"
    )
    trained_report = evaluate_ranking(
        _rank_all(trained_encoder, dev_rows, passages, limit=max(ks)), setting="dev-trained"
    )
    baseline = _headline(baseline_report, ks)
    trained = _headline(trained_report, ks)
    delta = {key: trained[key] - baseline[key] for key in trained}

    artifact_dir = encoder_artifact_dir(config)
    fingerprints = {
        "eval_set": eval_set_ref(eval_set_path).sha256,
        "corpus": corpus_fingerprint(passages),
        "train": split_fingerprint(train_rows),
        "dev": split_fingerprint(dev_rows),
        "calibration": split_fingerprint(grouped["calibration"]),
        "test": split_fingerprint(grouped["test"]),
    }
    metadata = {
        "trained_on_split": "train",
        "selected_on": training.select_on,
        "selected_on_split": "dev",
        "best_epoch": result.best_epoch,
        "dev_mrr": trained["mrr"],
        "dev_recall_at_1": trained["recall_at_1"],
        "dev_recall_at_3": trained["recall_at_3"],
        "dev_recall_at_5": trained["recall_at_5"],
        "baseline_dev_mrr": baseline["mrr"],
        "pairs": len(pairs),
        "hard_negatives": result.negatives,
        "split_seed": data.split_seed,
        "seed": config.seed,
        "phase": training.phase,
        **{f"fingerprint_{name}": value for name, value in fingerprints.items()},
    }
    trained_encoder.save(artifact_dir, metadata=metadata)
    _write_pairs(artifact_dir / PAIRS_FILE, pairs)

    elapsed = time.perf_counter() - started
    summary = TrainingSummary(
        artifact_dir=str(artifact_dir),
        encoder_name=trained_encoder.name,
        dim=result.dim,
        feature_dim=result.feature_dim,
        pairs=len(pairs),
        hard_negatives=result.negatives,
        tagged_negatives=mining.tag_preferring_choices,
        pairs_without_negatives=mining.zero_negative_pairs,
        best_epoch=result.best_epoch,
        select_on=training.select_on,
        baseline=baseline,
        trained=trained,
        delta=delta,
        history=tuple(_epoch_as_dict(record) for record in result.history),
        fingerprints=fingerprints,
        corpus_fingerprint=fingerprints["corpus"],
        seconds=elapsed,
        smoke=training.phase == "smoke",
    )
    (artifact_dir / "training_summary.json").write_text(
        json.dumps(summary.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if track:
        summary = _track(config, summary, config_path=config_path, seed_state=seed_state)
    _print(summary, mining)
    return summary


def _epoch_as_dict(record: EpochRecord) -> dict[str, Any]:
    return {
        "epoch": record.epoch,
        "train_loss": round(record.train_loss, 6),
        "steps": record.steps,
        "select_score": None if record.select_score is None else round(record.select_score, 6),
    }


def _write_pairs(path: Path, pairs: Sequence[TrainingPair]) -> None:
    """The pairs as JSONL, for the notebook and for review.

    Committed? No — it is derived data under the gitignored artefact directory.
    Its *hash* is what travels, in the run's params and in the report.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "query_id": pair.query_id,
                "query": pair.query,
                "lang": pair.lang,
                "positive": pair.positive,
                "positive_doc": pair.positive_doc,
                "hard_negatives": list(pair.hard_negatives),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for pair in pairs
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _track(
    config: Any,
    summary: TrainingSummary,
    *,
    config_path: Path | None,
    seed_state: Any,
) -> TrainingSummary:
    """Log the run, and return the summary with its run id filled in."""

    data_refs: list[DatasetRef] = [eval_set_ref(resolve(config.data.eval_set))]
    pairs_path = Path(summary.artifact_dir) / PAIRS_FILE
    if pairs_path.is_file():
        data_refs.append(
            dataset_ref(
                pairs_path,
                name="retrieval-training-pairs",
                text_field="query",
                label_field="positive_doc",
            )
        )
    params = {
        "encoder.kind": config.encoder.kind,
        "encoder.dim": config.encoder.dim,
        "training.loss": config.training.loss,
        "training.scale": config.training.scale,
        "training.epochs": config.training.epochs,
        "training.batch_size": config.training.batch_size,
        "training.lr": config.training.lr,
        "training.hard_negatives": config.training.hard_negatives,
        "training.mine_candidates": config.training.mine_candidates,
        "training.max_positives_per_query": config.training.max_positives_per_query,
        "training.select_on": config.training.select_on,
        "features.word_ngram_range": str(config.encoder.features.word_ngram_range),
        "features.char_ngram_range": str(config.encoder.features.char_ngram_range),
        "data.split_seed": config.data.split_seed,
        "pairs": summary.pairs,
        "feature_dim": summary.feature_dim,
        **{f"fingerprint.{name}": value for name, value in summary.fingerprints.items()},
    }
    tags = {"manovia.smoke": "true" if summary.smoke else "false", "manovia.stage": "encoder"}
    with tracked_run(
        config,
        phase=config.training.phase,
        data_refs=data_refs,
        seed_state=seed_state,
        params=params,
        tags=tags,
        config_path=config_path,
    ) as run:
        run.log_metrics(
            {
                **{f"dev/{key}": value for key, value in summary.trained.items()},
                **{f"dev_baseline/{key}": value for key, value in summary.baseline.items()},
                **{f"dev_delta/{key}": value for key, value in summary.delta.items()},
                "train/seconds": summary.seconds,
            }
        )
        run.log_dict(summary.as_dict(), "training_summary.json")
        if pairs_path.is_file():
            run.log_artifact(pairs_path, "retrieval")
        return dataclasses.replace(summary, run_id=run.run_id)


def _print(summary: TrainingSummary, mining: MiningReport) -> None:
    kind = "SMOKE (not a result)" if summary.smoke else "fine-tune"
    print(f"retrieval encoder {kind}: {summary.encoder_name} dim={summary.dim}")
    print(
        f"pairs={summary.pairs} hard_negatives={summary.hard_negatives} "
        f"(tag-preferring {summary.tagged_negatives}, none for "
        f"{summary.pairs_without_negatives})"
    )
    print(
        f"feature_dim={summary.feature_dim} best_epoch={summary.best_epoch} ({summary.select_on})"
    )
    for key in sorted(summary.trained):
        print(
            f"  dev {key}: baseline {summary.baseline[key]:.4f} -> "
            f"trained {summary.trained[key]:.4f} ({summary.delta[key]:+.4f})"
        )
    print(
        f"mining: {mining.queries} queries -> {mining.pairs} pairs, "
        f"positives/query {dict(sorted(mining.positives_per_query.items()))}, "
        f"negatives/pair {dict(sorted(mining.negatives_per_pair.items()))}"
    )
    print(f"wrote {summary.artifact_dir} in {summary.seconds:.1f}s")
    if summary.run_id:
        print(f"mlflow run {summary.run_id}")


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m manovia_ml.retrieval.train_embedding`` — train the encoder."""

    from manovia_ml.config import load_retrieval_config

    parser = argparse.ArgumentParser(
        description="Contrastively fine-tune the retrieval embedding (offline projection)."
    )
    parser.add_argument("--config", default="configs/retrieval_finetune.yaml")
    parser.add_argument(
        "--no-track", action="store_true", help="skip MLflow (same numbers, no run logged)"
    )
    args = parser.parse_args(argv)

    config_path = resolve(args.config)
    config = load_retrieval_config(config_path)
    if config.encoder.kind != "projection":
        raise SystemExit(
            f"encoder.kind is {config.encoder.kind!r}; this entry point trains the offline "
            "projection. A sentence-transformers checkpoint is trained by "
            "ml/notebooks/colab_retrieval_finetune.ipynb on the pairs this writes."
        )
    train_encoder(config, config_path=config_path, track=not args.no_track)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
