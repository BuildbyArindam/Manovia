"""Train a linear emotion classifier from a config, and log everything about it.

The model is deliberately modest: TF-IDF over word and character n-grams into a
linear classifier. Two reasons, both about the platform rather than accuracy:

* the whole point of Day 31 is the *machinery* — config, seed, data hash, run
  record, reproduction — and a model that trains in seconds on a CPU lets the
  machinery be tested in CI rather than in a notebook;
* a linear model is the right baseline for the hi/bn question anyway: if
  character n-grams cannot hold a signal on 60 hand-written sentences, a
  transformer will not fix the label scheme.

Real runs (GoEmotions, a real hi/bn set) use ``tfidf_logreg``; the smoke run uses
``tfidf_sgd`` with a small ``max_iter``, because the number of passes is exactly
what makes "same seed → same metrics, different seed → different metrics" a
true statement rather than a hoped-for one.

What gets logged per run (see ``manovia_ml.tracking``): git commit + dirty flag,
dataset sha256 and row counts, the resolved config and its fingerprint, the
seed, library versions, metrics, confusion matrix and the model bundle.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.pipeline import FeatureUnion, Pipeline

from manovia_ml.config import (
    ConfigError,
    ExpectedMetrics,
    LogRegConfig,
    SgdConfig,
    TrainConfig,
    compare_metrics,
    config_fingerprint,
    load_config,
    load_expected_metrics,
    write_expected_metrics,
)
from manovia_ml.evaluation.metrics import (
    classification_metrics,
    confusion,
    majority_baseline_accuracy,
    per_class_f1,
)
from manovia_ml.hashing import DatasetRef, check_expected, dataset_ref, read_jsonl
from manovia_ml.paths import resolve
from manovia_ml.seeds import SeedState, seed_everything
from manovia_ml.serving.artifacts import BUNDLE_FILENAME, save_bundle
from manovia_ml.tracking import tracked_run

__all__ = ["TrainResult", "build_pipeline", "main", "train"]


@dataclass
class TrainResult:
    run_id: str
    metrics: dict[str, float]
    per_class: dict[str, float]
    matrix: list[list[int]]
    labels: list[str]
    seed_state: SeedState
    train_ref: DatasetRef
    eval_ref: DatasetRef
    expected_problems: list[str] = field(default_factory=list)
    expected_checked: bool = False
    recorded_expected_path: Path | None = None


def _features(config: TrainConfig) -> Pipeline:
    model = config.model
    if not isinstance(model, (SgdConfig, LogRegConfig)):
        raise ConfigError("_features() is for the linear model kinds")
    tfidf = model.tfidf
    word = TfidfVectorizer(
        analyzer="word",
        ngram_range=tfidf.word_ngram_range,
        min_df=tfidf.min_df,
        max_features=tfidf.max_features,
        sublinear_tf=tfidf.sublinear_tf,
        lowercase=tfidf.lowercase,
    )
    if tfidf.char_ngram_range is None:
        return Pipeline([("features", word)])
    char = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=tfidf.char_ngram_range,
        min_df=tfidf.min_df,
        max_features=tfidf.max_features,
        sublinear_tf=tfidf.sublinear_tf,
        lowercase=tfidf.lowercase,
    )
    # A word model catches "panicked"; a character model catches "panickd",
    # "panik", and the romanised spelling a person actually types at 2am.
    return Pipeline([("features", FeatureUnion([("word", word), ("char", char)]))])


def build_pipeline(config: TrainConfig, *, seed: int) -> Pipeline:
    """The sklearn pipeline described by the config, seeded with ``seed``."""

    model = config.model
    if isinstance(model, SgdConfig):
        classifier: Any = SGDClassifier(
            loss="log_loss",
            alpha=model.alpha,
            max_iter=model.max_iter,
            random_state=seed,
            average=model.average,
            class_weight=model.class_weight,
            tol=None,  # exactly `max_iter` passes: "how many steps" is a config decision
        )
        kind = "SGDClassifier"
    elif isinstance(model, LogRegConfig):
        classifier = LogisticRegression(
            C=model.C,
            max_iter=model.max_iter,
            solver=model.solver,
            random_state=seed,
            class_weight=model.class_weight,
        )
        kind = "LogisticRegression"
    else:  # pragma: no cover - the discriminated union makes this unreachable
        raise ConfigError(f"unknown model kind: {model!r}")
    return Pipeline([*_features(config).steps, (kind, classifier)])


def model_params(config: TrainConfig) -> dict[str, Any]:
    """The hyperparameters, flattened for MLflow params (one nesting level).

    Only the linear kinds reach here; an emotion run logs its own parameters in
    ``training/emotion/train.py``.
    """

    model = config.model
    if not isinstance(model, (SgdConfig, LogRegConfig)):
        raise ConfigError("model_params() is for the linear model kinds")
    tfidf = model.tfidf
    params: dict[str, Any] = {
        "model.kind": model.kind,
        "tfidf.word_ngram_range": list(tfidf.word_ngram_range),
        "tfidf.char_ngram_range": list(tfidf.char_ngram_range)
        if tfidf.char_ngram_range
        else "none",
        "tfidf.min_df": tfidf.min_df,
        "tfidf.max_features": tfidf.max_features if tfidf.max_features is not None else "none",
        "tfidf.sublinear_tf": tfidf.sublinear_tf,
        "tfidf.lowercase": tfidf.lowercase,
        "evaluation.tolerance": config.evaluation.tolerance,
    }
    if isinstance(model, SgdConfig):
        params |= {
            "model.alpha": model.alpha,
            "model.max_iter": model.max_iter,
            "model.average": model.average,
        }
    else:
        params |= {
            "model.C": model.C,
            "model.max_iter": model.max_iter,
            "model.solver": model.solver,
        }
    params["model.class_weight"] = model.class_weight or "none"
    return params


def _class_labels(config: TrainConfig, train_rows: list[dict[str, Any]]) -> list[str]:
    seen = {str(row[config.data.label_field]) for row in train_rows}
    labels = list(config.data.labels) if config.data.labels else sorted(seen)
    missing = sorted(seen - set(labels))
    if missing:
        raise ConfigError(f"training data contains labels not in config.data.labels: {missing}")
    absent = sorted(set(labels) - seen)
    if absent:
        raise ConfigError(f"config.data.labels lists labels with no training rows: {absent}")
    return labels


def _load_split(
    config: TrainConfig, path: str, *, name: str
) -> tuple[list[dict[str, Any]], DatasetRef]:
    resolved = resolve(path)
    rows = read_jsonl(resolved)
    ref = dataset_ref(
        resolved,
        name=name,
        text_field=config.data.text_field,
        label_field=config.data.label_field,
        lang_field=config.data.lang_field,
        rows=rows,
    )
    spec = config.data.train if name == "train" else config.data.eval
    check_expected(ref, sha256=spec.sha256, rows=spec.rows)
    return rows, ref


def train(
    config: TrainConfig,
    *,
    config_path: Path | str | None = None,
    phase: str = "train",
    tags: dict[str, str] | None = None,
    check_expected: bool = True,
    record_expected: bool = False,
) -> TrainResult:
    """Run the whole thing: seed, read, fit, score, log. Returns the record."""

    seed_state = seed_everything(config.seed, pin_blas_threads=config.threads)

    train_rows, train_ref = _load_split(config, config.data.train.path, name="train")
    eval_rows, eval_ref = _load_split(config, config.data.eval.path, name="eval")
    labels = _class_labels(config, train_rows)

    text_field = config.data.text_field
    label_field = config.data.label_field
    x_train = [str(row[text_field]) for row in train_rows]
    y_train = [str(row[label_field]) for row in train_rows]
    x_eval = [str(row[text_field]) for row in eval_rows]
    y_eval = [str(row[label_field]) for row in eval_rows]

    pipeline = build_pipeline(config, seed=config.seed)
    pipeline.fit(x_train, y_train)
    predictions = [str(label) for label in pipeline.predict(x_eval)]

    probabilities: list[list[float]] | None = None
    if "log_loss" in config.evaluation.metrics:
        if not hasattr(pipeline, "predict_proba"):
            raise ConfigError(
                f"{config.name}: metric `log_loss` needs predict_proba, but "
                f"{config.model.kind} with average=True does not provide it"
            )
        # Columns come back in the estimator's class order (lexicographic), which
        # is not necessarily the config's label order. Read them positionally and
        # every probability is silently attributed to the wrong label — caught
        # here by ml/tests/test_eval_serving.py on its first run, which is what
        # that check exists for.
        classes = [str(label) for label in pipeline.classes_]
        missing = [label for label in labels if label not in classes]
        if missing:
            raise ConfigError(f"{config.name}: model has no probabilities for {missing}")
        probabilities = [
            [float(row[classes.index(label)]) for label in labels]
            for row in pipeline.predict_proba(x_eval)
        ]

    metrics = classification_metrics(
        y_eval,
        predictions,
        labels=labels,
        metrics=config.evaluation.metrics,
        probabilities=probabilities,
    )
    # In-sample accuracy, logged always: on a toy fixture it is the meaningful
    # canary (a pipeline that can no longer fit its own 20 rows is broken in a
    # way that held-out accuracy on 8 rows cannot distinguish from noise), and on
    # a real corpus it is the first thing to look at when a metric moves.
    # The floor a model has to clear before accuracy means anything (see
    # manovia_ml.evaluation.metrics.majority_baseline_accuracy). Always logged.
    metrics["majority_accuracy"] = majority_baseline_accuracy(y_eval)
    metrics["train_accuracy"] = classification_metrics(
        y_train,
        [str(label) for label in pipeline.predict(x_train)],
        labels=labels,
        metrics=["accuracy"],
    )["accuracy"]
    per_class = per_class_f1(y_eval, predictions, labels=labels)
    matrix = (
        confusion(y_eval, predictions, labels=labels) if config.evaluation.confusion_matrix else []
    )

    params = {
        **model_params(config),
        "data.train.rows": len(train_rows),
        "data.eval.rows": len(eval_rows),
        "labels": labels,
        "dataset": config.data.train.path,
    }

    expected = load_expected_metrics(config_path) if (config_path and check_expected) else None
    problems: list[str] = []
    if expected is not None:
        if expected.seed != config.seed:
            print(
                f"note: baseline {expected.config} was recorded with seed {expected.seed}, "
                f"this run uses {config.seed} — baseline check skipped",
                file=sys.stderr,
            )
            expected = None
        else:
            problems = compare_metrics(
                metrics, expected.metrics, tolerance=config.evaluation.tolerance
            )

    recorded_path: Path | None = None
    with tempfile.TemporaryDirectory(prefix="manovia-ml-") as tmp:
        bundle_path = save_bundle(
            Path(tmp) / BUNDLE_FILENAME,
            pipeline=pipeline,
            labels=labels,
            meta={
                "config": config.name,
                "config_sha256": config_fingerprint(config),
                "phase": phase,
                "seed": config.seed,
                "labels": labels,
                "trained_on": {
                    "path": train_ref.path,
                    "sha256": train_ref.sha256,
                    "rows": train_ref.rows,
                },
                "evaluated_on": {
                    "path": eval_ref.path,
                    "sha256": eval_ref.sha256,
                    "rows": eval_ref.rows,
                },
            },
        )
        bundle_size = bundle_path.stat().st_size

        with tracked_run(
            config,
            phase=phase,
            data_refs=(train_ref, eval_ref),
            seed_state=seed_state,
            params=params,
            tags=tags,
            config_path=Path(config_path) if config_path else None,
        ) as run:
            run.log_metrics(metrics)
            # Per-class F1 is logged as `f1.<label>`, never as the bare label: in a
            # metrics table "anger 0.000" next to "accuracy 0.375" reads like two
            # comparable numbers rather than a class name and its score.
            run.log_metrics(
                {
                    f"f1.{name}": value
                    for name, value in per_class.items()
                    if not name.startswith("support.")
                }
            )
            run.log_params(
                {
                    f"support.{key}": value
                    for key, value in per_class.items()
                    if key.startswith("support.")
                }
            )
            run.log_dict(
                {
                    "labels": labels,
                    "confusion_matrix": {"labels": labels, "rows": matrix},
                    "per_class_f1": per_class,
                    "metrics": metrics,
                },
                "evaluation.json",
            )
            run.log_dict({"metrics": metrics, "per_class_f1": per_class}, "metrics.json")
            run.log_dict(
                {
                    "bundle_bytes": bundle_size,
                    "model_artifact": f"model/{BUNDLE_FILENAME}",
                    "labels": labels,
                },
                "model/manifest.json",
            )
            run.log_artifact(bundle_path, artifact_path="model")
            if expected is not None:
                run.log_params(
                    {
                        "expected.checked": "true",
                        "expected.problems": len(problems),
                    }
                )
                run.log_dict(
                    {"baseline": expected.model_dump(mode="json"), "problems": problems},
                    "expected.json",
                )
            if problems:
                run.set_tags({"manovia.expected": "failed"})
            run_id = run.run_id

    if record_expected and config_path:
        recorded_path = write_expected_metrics(
            config_path,
            ExpectedMetrics(
                config=Path(config_path).name,
                seed=config.seed,
                tolerance=config.evaluation.tolerance,
                metrics=metrics,
                run_id=run_id,
                recorded=datetime.now(UTC).date().isoformat(),
                note=(
                    "Recorded by `make ml-record`. Every later run of this config with this seed "
                    "must match within `tolerance`."
                ),
            ),
        )

    return TrainResult(
        run_id=run_id,
        metrics=metrics,
        per_class=per_class,
        matrix=matrix,
        labels=labels,
        seed_state=seed_state,
        train_ref=train_ref,
        eval_ref=eval_ref,
        expected_problems=problems,
        expected_checked=expected is not None,
        recorded_expected_path=recorded_path,
    )


def _print_summary(result: TrainResult, config: TrainConfig) -> None:
    print(f"RUN_ID={result.run_id}")
    print(f"config={config.name} seed={config.seed} kind={config.model.kind}")
    print(
        f"data: {result.train_ref.rows} train rows sha256={result.train_ref.sha256[:12]}… | "
        f"{result.eval_ref.rows} eval rows sha256={result.eval_ref.sha256[:12]}…"
    )
    print(
        "metrics: "
        + " ".join(f"{name}={value:.6f}" for name, value in sorted(result.metrics.items()))
    )
    if result.recorded_expected_path:
        print(f"baseline recorded: {result.recorded_expected_path}")
    if result.expected_problems:
        print("baseline drift:", file=sys.stderr)
        for problem in result.expected_problems:
            print(f"  - {problem}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.training.train",
        description="Train the emotion classifier described by a YAML config and log the run.",
    )
    parser.add_argument("--config", required=True, help="e.g. configs/toy_emotion_sgd.yaml")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="override the config's seed for this run (the baseline check is skipped, by design)",
    )
    parser.add_argument("--phase", default="train", help="MLflow phase tag (train/smoke)")
    parser.add_argument(
        "--record-expected",
        action="store_true",
        help="write ml/configs/expected/<config>.json from this run (deliberate act)",
    )
    parser.add_argument("--tag", action="append", default=[], help="extra tag key=value")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        if args.seed is not None:
            if not 0 <= args.seed < 2**32:
                raise ConfigError(f"--seed must fit in 4 bytes, got {args.seed}")
            config = config.model_copy(update={"seed": args.seed})
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    tags = {}
    for item in args.tag:
        key, _, value = item.partition("=")
        tags[key] = value

    result = train(
        config,
        config_path=args.config,
        phase=args.phase,
        tags=tags,
        record_expected=args.record_expected,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "metrics": result.metrics,
                    "seed": config.seed,
                    "config_sha256": config_fingerprint(config),
                    "data": {
                        "train_sha256": result.train_ref.sha256,
                        "eval_sha256": result.eval_ref.sha256,
                    },
                    "expected_problems": result.expected_problems,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _print_summary(result, config)

    return 1 if result.expected_problems else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
