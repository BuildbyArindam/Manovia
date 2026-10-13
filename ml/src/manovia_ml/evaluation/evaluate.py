"""Re-score a logged run from its own artefacts.

``make ml-eval RUN=<run id>`` needs nothing else: the run's ``config.json``
artefact *is* the config, so the eval split, the label order, the metric list and
the tolerance all come from what the training run actually used. That is the
property worth having — a metric can only be reproduced from the same inputs.

Three checks run here, and each one has caught a real class of bug somewhere:

1. **The eval split still hashes to what the config pinned** — otherwise the
   number describes data the model never saw.
2. **The re-scored metrics equal the metrics the training run logged.** They were
   computed at different times, from the same model and the same rows; a
   disagreement means the pipeline is not deterministic or the split moved.
3. **The bundle's scikit-learn version matches** (``serving.artifacts``), because
   unpickling across versions is how a model quietly becomes a different model.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from manovia_ml.config import ConfigError, TrainConfig, compare_metrics, load_config_dict
from manovia_ml.evaluation.metrics import classification_metrics, confusion, per_class_f1
from manovia_ml.hashing import check_expected, dataset_ref, read_jsonl
from manovia_ml.mlflow_api import MlflowClient, mlflow
from manovia_ml.paths import default_tracking_uri, resolve
from manovia_ml.serving.artifacts import BUNDLE_FILENAME, load_bundle
from manovia_ml.tracking import tracked_run

__all__ = ["EvalResult", "evaluate_run", "main"]


@dataclass
class EvalResult:
    eval_run_id: str
    scored_run_id: str
    metrics: dict[str, float]
    per_class: dict[str, float]
    matrix: list[list[int]]
    labels: list[str]
    train_metrics: dict[str, float] = field(default_factory=dict)
    consistency_problems: list[str] = field(default_factory=list)
    config_name: str = ""
    eval_path: str = ""
    eval_sha256: str = ""
    eval_rows: int = 0

    @property
    def consistent(self) -> bool:
        return not self.consistency_problems


def _download_artifact(run_id: str, artifact_path: str, destination: Path) -> Path:
    downloaded = mlflow.artifacts.download_artifacts(
        run_id=run_id, artifact_path=artifact_path, dst_path=str(destination)
    )
    return Path(str(downloaded))


def load_run_config(run_id: str, *, destination: Path) -> TrainConfig:
    """Rebuild the run's config from the ``config.json`` artefact it logged."""

    path = _download_artifact(run_id, "config.json", destination)
    if path.is_dir():  # pragma: no cover - defensive: a directory would mean a store bug
        path = path / "config.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return load_config_dict(raw, origin=f"run {run_id} config.json")


def evaluate_run(
    run_id: str,
    *,
    tracking_uri: str | None = None,
    tags: dict[str, str] | None = None,
) -> EvalResult:
    uri = tracking_uri or default_tracking_uri()
    mlflow.set_tracking_uri(uri)
    client = MlflowClient()
    train_run = client.get_run(run_id)
    train_metrics = {name: float(value) for name, value in train_run.data.metrics.items()}

    with tempfile.TemporaryDirectory(prefix="manovia-ml-eval-") as tmp:
        tmp_path = Path(tmp)
        config = load_run_config(run_id, destination=tmp_path / "config")
        bundle_path = _download_artifact(run_id, f"model/{BUNDLE_FILENAME}", tmp_path / "model")
        if bundle_path.is_dir():
            bundle_path = bundle_path / BUNDLE_FILENAME
        bundle = load_bundle(bundle_path)

        eval_resolved = resolve(config.data.eval.path)
        rows = read_jsonl(eval_resolved)
        ref = dataset_ref(
            eval_resolved,
            name="eval",
            text_field=config.data.text_field,
            label_field=config.data.label_field,
            lang_field=config.data.lang_field,
            rows=rows,
        )
        check_expected(ref, sha256=config.data.eval.sha256, rows=config.data.eval.rows)

        labels = bundle.labels
        x_eval = [str(row[config.data.text_field]) for row in rows]
        y_eval = [str(row[config.data.label_field]) for row in rows]
        predictions = bundle.predict(x_eval)

        probabilities: list[list[float]] | None = None
        if "log_loss" in config.evaluation.metrics:
            per_row = bundle.predict_proba(x_eval)
            probabilities = [[row[label] for label in labels] for row in per_row]

        metrics = classification_metrics(
            y_eval,
            predictions,
            labels=labels,
            metrics=config.evaluation.metrics,
            probabilities=probabilities,
        )
        per_class = per_class_f1(y_eval, predictions, labels=labels)
        matrix = confusion(y_eval, predictions, labels=labels)

        consistency = compare_metrics(
            metrics,
            {name: train_metrics[name] for name in metrics if name in train_metrics},
            tolerance=config.evaluation.tolerance,
        )

        params = {
            "eval_of": run_id,
            "configname": config.name,
            "eval.rows": ref.rows,
            "bundle.artifact": f"model/{BUNDLE_FILENAME}",
        }
        with tracked_run(
            config,
            phase="eval",
            data_refs=(ref,),
            params=params,
            tags={"manovia.eval_of": run_id, **(tags or {})},
            parent_run_id=run_id,
        ) as run:
            run.log_metrics(metrics)
            run.log_metrics(
                {
                    name: value
                    for name, value in per_class.items()
                    if not name.startswith("support.")
                }
            )
            run.log_dict(
                {
                    "eval_of": run_id,
                    "labels": labels,
                    "metrics": metrics,
                    "per_class_f1": per_class,
                    "confusion_matrix": {"labels": labels, "rows": matrix},
                    "training_run_metrics": {
                        name: train_metrics.get(name) for name in sorted(metrics)
                    },
                    "consistency_problems": consistency,
                },
                "eval.json",
            )
            if consistency:
                run.set_tags({"manovia.consistency": "failed"})
            eval_run_id = run.run_id

    return EvalResult(
        eval_run_id=eval_run_id,
        scored_run_id=run_id,
        metrics=metrics,
        per_class=per_class,
        matrix=matrix,
        labels=labels,
        train_metrics=train_metrics,
        consistency_problems=consistency,
        config_name=config.name,
        eval_path=ref.path,
        eval_sha256=ref.sha256,
        eval_rows=ref.rows,
    )


def _print_summary(result: EvalResult) -> None:
    print(f"EVAL_RUN_ID={result.eval_run_id}")
    print(f"scored run={result.scored_run_id} config={result.config_name}")
    print(f"split: {result.eval_path} rows={result.eval_rows} sha256={result.eval_sha256[:12]}…")
    print(
        "metrics: "
        + " ".join(f"{name}={value:.6f}" for name, value in sorted(result.metrics.items()))
    )
    if result.consistent:
        print("consistency: this run's metrics match the training run's logged metrics")
    else:
        print("consistency: MISMATCH", file=sys.stderr)
        for problem in result.consistency_problems:
            print(f"  - {problem}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.evaluation.evaluate",
        description="Score a logged run on the eval split named in its own config.json.",
    )
    parser.add_argument("--run", required=True, help="MLflow run id (from `make ml-train`)")
    parser.add_argument("--tracking-uri", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--tag", action="append", default=[])
    args = parser.parse_args(argv)

    tags = {}
    for item in args.tag:
        key, _, value = item.partition("=")
        tags[key] = value

    try:
        result = evaluate_run(args.run, tracking_uri=args.tracking_uri, tags=tags)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "eval_run_id": result.eval_run_id,
                    "scored_run_id": result.scored_run_id,
                    "metrics": result.metrics,
                    "consistency_problems": result.consistency_problems,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _print_summary(result)
    return 0 if result.consistent else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
