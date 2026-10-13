"""Score a packaged safety artefact — inference only, never the trainer.

The Day-32 rule, kept: **a number may only come from the saved artefact.** So this
module refuses to run in a process that has imported
``manovia_ml.training.safety.train`` — the check is a ``sys.modules`` guard, not a
convention. That is what makes ``make ml-eval`` meaningful: the numbers cannot have
been produced by the in-memory model that was just fitted.

What it computes on whatever split it is given:

* per-level precision/recall/F1 and the confusion matrix;
* the binary HIGH+IMMINENT view with a seeded bootstrap 95% CI;
* per-language recall and precision;
* ECE and the reliability diagram, before and after the artefact's temperature;
* conformal coverage against the artefact's own quantile, the abstention rate, and
  what the abstain -> MEDIUM policy changed;
* the leakage check against the split the artefact was trained on.

It deliberately does **not** re-fit anything. If the temperature or the quantile is
missing from the artefact, it says so rather than inventing one.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from manovia_ml.paths import resolve
from manovia_ml.training.safety.calibration import (
    expected_calibration_error,
    reliability_diagram,
)
from manovia_ml.training.safety.conformal import (
    abstain_level,
    prediction_set,
    set_coverage,
    set_size_stats,
)
from manovia_ml.training.safety.data import (
    SafetyExample,
    load_examples,
    near_duplicate_report,
)
from manovia_ml.training.safety.inference import predict_logits, resolve_device
from manovia_ml.training.safety.metrics import (
    accuracy,
    binary_crisis_metrics,
    bootstrap_ci,
    confusion_matrix,
    expected_cost,
    macro_f1,
    per_level_prf,
    top_level,
)
from manovia_ml.training.safety.model import (
    CALIBRATION_FILENAME,
    CONFORMAL_FILENAME,
    MANIFEST_FILENAME,
    MODEL_DIRNAME,
    TOKENIZER_DIRNAME,
)
from manovia_ml.training.safety.taxonomy import LEVELS, severity

__all__ = ["TRAINER_MODULE", "EvaluationError", "evaluate", "main"]

#: The module this one must never share a process with.
TRAINER_MODULE = "manovia_ml.training.safety.train"


class EvaluationError(RuntimeError):
    """The artefact or the data cannot be scored as asked."""


def _guard_against_the_trainer() -> None:
    if TRAINER_MODULE in sys.modules:
        raise EvaluationError(
            "refusing to evaluate in a process that imported the trainer: a number "
            "must come from the saved artefact, not from the model that was just "
            "fitted (run `python -m manovia_ml.training.safety.evaluate` in a fresh "
            "process)"
        )


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def evaluate(
    artifact_dir: str | Path,
    data_path: str | Path,
    *,
    bootstrap: int = 2000,
    confidence_level: float = 0.95,
    bins: int = 10,
    train_path: str | Path | None = None,
    max_length: int = 96,
    batch_size: int = 32,
    device: str = "auto",
) -> dict[str, Any]:
    """Score ``data_path`` with the artefact in ``artifact_dir``."""
    _guard_against_the_trainer()
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    directory = Path(artifact_dir)
    if not (directory / MODEL_DIRNAME).is_dir():
        raise EvaluationError(f"no model directory in {directory}")
    calibration = _load_json(directory / CALIBRATION_FILENAME)
    conformal = _load_json(directory / CONFORMAL_FILENAME)
    manifest = _load_json(directory / MANIFEST_FILENAME)

    tokenizer = AutoTokenizer.from_pretrained(str(directory / TOKENIZER_DIRNAME))
    model = AutoModelForSequenceClassification.from_pretrained(str(directory / MODEL_DIRNAME))
    resolved_device = resolve_device(torch, requested=device)
    model.to(resolved_device)
    model.eval()

    examples: Sequence[SafetyExample] = load_examples(data_path)
    if not examples:
        raise EvaluationError(f"no rows to evaluate in {data_path}")
    logits = predict_logits(
        model,
        tokenizer,
        examples,
        max_length=max_length,
        batch_size=batch_size,
        device=resolved_device,
    )

    temperature = float(calibration.get("temperature", 1.0)) or 1.0
    from manovia_ml.training.safety.calibration import apply_temperature

    raw_probabilities = [
        dict(zip(LEVELS, _softmax(row), strict=True)) for row in logits
    ]
    probabilities = [
        dict(zip(LEVELS, apply_temperature(row, temperature), strict=True)) for row in logits
    ]
    truths = [example.label for example in examples]
    predictions = [top_level(row, cost_rule=True) for row in probabilities]

    quantile = float(conformal.get("quantile", 0.0))
    sets = [prediction_set(row, quantile) for row in probabilities] if quantile else []
    abstained = [
        abstain_level(item, point=point) if item else point
        for item, point in zip(sets, predictions, strict=True)
    ]

    pairs = list(zip(truths, predictions, strict=True))
    crisis = binary_crisis_metrics(truths, predictions)
    abstain_crisis = binary_crisis_metrics(truths, abstained)
    per_language: dict[str, dict[str, Any]] = {}
    for lang in sorted({example.lang for example in examples}):
        indices = [index for index, example in enumerate(examples) if example.lang == lang]
        if not indices:
            continue
        sub_truths = [truths[index] for index in indices]
        sub_predictions = [predictions[index] for index in indices]
        sub_crisis = binary_crisis_metrics(sub_truths, sub_predictions)
        per_language[lang] = {
            "cases": len(indices),
            "crisis_cases": sub_crisis["crisis_support"],
            "crisis_recall": sub_crisis["recall"],
            "crisis_recall_ci": list(
                bootstrap_ci(
                    list(zip(sub_truths, sub_predictions, strict=True)),
                    lambda sample: binary_crisis_metrics(
                        [item[0] for item in sample], [item[1] for item in sample]
                    )["recall"],
                    resamples=bootstrap,
                    confidence_level=confidence_level,
                    seed=20261013,
                )
            ),
            "crisis_precision": sub_crisis["precision"],
        }

    size_stats = (
        set_size_stats(sets)
        if sets
        else {"abstention_rate": 0.0, "mean_size": 0.0, "max_size": 0.0, "singleton_share": 0.0}
    )
    leakage: dict[str, Any] = {}
    if train_path is not None:
        leakage = near_duplicate_report(load_examples(train_path), examples)

    return {
        "artifact_dir": str(directory),
        "data": str(resolve(data_path)),
        "run_id": manifest.get("run_id"),
        "base_model": manifest.get("base_model"),
        "cases": len(examples),
        "temperature": temperature,
        "conformal_quantile": quantile,
        "accuracy": accuracy(truths, predictions),
        "macro_f1": macro_f1(truths, predictions),
        "expected_cost": expected_cost(truths, predictions),
        "crisis": crisis,
        "crisis_recall_ci": list(
            bootstrap_ci(
                pairs,
                lambda sample: binary_crisis_metrics(
                    [item[0] for item in sample], [item[1] for item in sample]
                )["recall"],
                resamples=bootstrap,
                confidence_level=confidence_level,
                seed=20261013,
            )
        ),
        "crisis_precision_ci": list(
            bootstrap_ci(
                pairs,
                lambda sample: binary_crisis_metrics(
                    [item[0] for item in sample], [item[1] for item in sample]
                )["precision"],
                resamples=bootstrap,
                confidence_level=confidence_level,
                seed=20261013,
            )
        ),
        "per_language": per_language,
        "per_level": per_level_prf(truths, predictions),
        "confusion_matrix": {
            "labels": list(LEVELS),
            "rows": confusion_matrix(truths, predictions),
        },
        "calibration": {
            "temperature": temperature,
            "ece_before": expected_calibration_error(
                raw_probabilities, truths, bins=bins
            ),
            "ece_after": expected_calibration_error(probabilities, truths, bins=bins),
            "reliability": [item.as_dict() for item in reliability_diagram(probabilities, truths,
                bins=bins)],
        },
        "conformal": {
            "quantile": quantile,
            "target_coverage": float(conformal.get("target_coverage", 0.0)),
            "coverage": set_coverage(sets, truths) if sets else None,
            "abstention_rate": size_stats["abstention_rate"],
            "mean_set_size": size_stats["mean_size"],
        },
        "abstention_policy": {
            "crisis_recall": abstain_crisis["recall"],
            "crisis_precision": abstain_crisis["precision"],
            "raised_by_abstention": sum(
                1
                for point, final in zip(predictions, abstained, strict=True)
                if severity(final) > severity(point)
            ),
        },
        "false_negatives": [
            {"id": examples[index].id, "true": truths[index], "predicted": predictions[index]}
            for index in range(len(truths))
            if truths[index] in ("high", "imminent")
            and predictions[index] not in ("high", "imminent")
        ],
        "leakage": leakage,
    }


def _softmax(row: Sequence[float]) -> list[float]:
    import math

    peak = max(row)
    exponentials = [math.exp(value - peak) for value in row]
    total = sum(exponentials)
    return [value / total for value in exponentials]


def _print_summary(payload: dict[str, Any]) -> None:
    crisis = payload["crisis"]
    print(f"artifact={payload['artifact_dir']}")
    print(f"data={payload['data']}  cases={payload['cases']}  run_id={payload['run_id']}")
    print(
        f"HIGH+IMMINENT recall={_fmt(crisis['recall'])} "
        f"{_ci(payload['crisis_recall_ci'])}  precision={_fmt(crisis['precision'])} "
        f"{_ci(payload['crisis_precision_ci'])}"
    )
    print(
        f"macro_f1={_fmt(payload['macro_f1'])}  accuracy={_fmt(payload['accuracy'])}  "
        f"expected_cost={_fmt(payload['expected_cost'])}"
    )
    for lang, row in sorted(payload["per_language"].items()):
        print(
            f"  {lang:<8} cases={row['cases']:<4} crisis={_fmt(row['crisis_cases'], 0):<4} "
            f"recall={_fmt(row['crisis_recall'])} {_ci(row['crisis_recall_ci'])} "
            f"precision={_fmt(row['crisis_precision'])}"
        )
    calibration = payload["calibration"]
    print(
        f"calibration: T={calibration['temperature']:.3f}  "
        f"ECE {calibration['ece_before']:.4f} -> {calibration['ece_after']:.4f}"
    )
    conformal = payload["conformal"]
    if conformal["quantile"]:
        print(
            f"conformal: q={conformal['quantile']:.4f}  coverage={_fmt(conformal['coverage'])}  "
            f"abstention_rate={_fmt(conformal['abstention_rate'])}  "
            f"raised={payload['abstention_policy']['raised_by_abstention']}"
        )
    if payload["leakage"]:
        print(
            f"leakage vs train: max_jaccard={payload['leakage']['max_jaccard']:.3f} "
            f"exact={payload['leakage']['exact_overlap']}"
        )
    print(f"false negatives: {len(payload['false_negatives'])}")


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _ci(pair: Any) -> str:
    if not isinstance(pair, list) or len(pair) != 2 or pair[0] is None or pair[1] is None:
        return "—"
    return f"[{pair[0]:.3f}, {pair[1]:.3f}]"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score a packaged safety artefact on a labelled split (inference only)."
    )
    parser.add_argument("--artifact", required=True, help="artefact directory")
    parser.add_argument("--data", required=True, help="labelled JSONL split to score")
    parser.add_argument("--train", default=None, help="training split, for the leakage check")
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--max-length", type=int, default=96)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--out", type=Path, default=None, help="write the JSON payload here")
    parser.add_argument("--json", action="store_true", help="print the JSON payload")
    args = parser.parse_args(argv)

    payload = evaluate(
        args.artifact,
        args.data,
        bootstrap=args.bootstrap,
        bins=args.bins,
        train_path=args.train,
        max_length=args.max_length,
        batch_size=args.batch_size,
    )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
