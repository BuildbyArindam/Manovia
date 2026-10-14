"""The Day 33 run: cost-sensitive LoRA training, calibration, conformal, report.

The loop is written out rather than wrapped in ``Trainer`` for the same reason
Day 32's is: every choice the brief asks for is then a line someone can read.

| requirement | where it is |
| --- | --- |
| cost-sensitive loss | :func:`_loss_for_batch`, which calls
  :func:`manovia_ml.training.safety.cost.cost_weighted_cross_entropy` |
| documented cost matrix | ``taxonomy.COST_MATRIX`` + ``cost.COST_MATRIX_DOC``,
    printed into the report and the card |
| temperature scaling + ECE + reliability diagram |
  :mod:`manovia_ml.training.safety.calibration`,
    fitted on the **calibration** split |
| split conformal prediction sets at 95% coverage | :mod:`manovia_ml.training.safety.conformal`,
    quantile from the **calibration** split |
| abstention policy | ``conformal.abstain_level`` — a set containing MEDIUM or above
  makes the message MEDIUM or above |
| TF-IDF baseline comparison | :mod:`manovia_ml.training.safety.baselines`, fit on the same rows |
| MLflow provenance | ``manovia_ml.tracking.tracked_run``: git commit, data hashes,
    config fingerprint, seed, library versions, metrics, artefacts |

Split discipline, stated once and enforced by the config validator: ``data.train``
is fit, ``data.eval`` is dev (model selection), ``data.validation`` is the
calibration split, and ``data.test`` is the frozen Day-9 file — read here, scored
here, and never trained on or tuned on.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from manovia_ml.config import (
    ConfigError,
    ExpectedMetrics,
    SafetyLoraConfig,
    SafetyRunConfig,
    TrainConfig,
    compare_metrics,
    config_fingerprint,
    load_config,
    load_expected_metrics,
    write_expected_metrics,
)
from manovia_ml.hashing import DatasetRef
from manovia_ml.paths import resolve
from manovia_ml.seeds import SeedState, derive_seed, seed_everything, seeded_rng
from manovia_ml.tracking import tracked_run
from manovia_ml.training.safety import metrics as metric_fns
from manovia_ml.training.safety.calibration import (
    DEFAULT_BINS,
    CalibrationResult,
    calibrate,
    expected_calibration_error,
)
from manovia_ml.training.safety.conformal import (
    ConformalResult,
    abstain_level,
    calibrate_conformal,
    prediction_set,
    set_coverage,
    set_size_stats,
)
from manovia_ml.training.safety.cost import cost_matrix_rows
from manovia_ml.training.safety.data import (
    SafetyExample,
    dataset_ref,
    load_examples,
    near_duplicate_report,
)
from manovia_ml.training.safety.inference import predict_logits, resolve_device
from manovia_ml.training.safety.model import (
    CALIBRATION_FILENAME,
    CARD_FILENAME,
    CONFORMAL_FILENAME,
    MANIFEST_FILENAME,
    MODEL_DIRNAME,
    THRESHOLDS_FILENAME,
    TOKENIZER_DIRNAME,
    build_lora_model,
    merge_and_save,
    trainable_parameter_report,
)
from manovia_ml.training.safety.taxonomy import (
    COST_MATRIX_DOC,
    LEVEL_INDEX,
    LEVELS,
    class_weights_from_cost,
    severity,
)

__all__ = ["SafetyTrainResult", "main", "train"]


@dataclass
class SafetyTrainResult:
    """Everything the CLI, the tests and the report need from one run."""

    run_id: str
    artifact_dir: Path
    metrics: dict[str, float]
    calibration: CalibrationResult
    conformal: ConformalResult
    baseline: dict[str, Any]
    dev_evaluation: dict[str, Any]
    history: list[dict[str, float]]
    seed_state: SeedState
    data_refs: tuple[DatasetRef, ...]
    config: TrainConfig
    leakage: dict[str, Any]
    expected_problems: list[str] = field(default_factory=list)
    expected_checked: bool = False
    recorded_expected_path: Path | None = None
    base_model: str = ""
    elapsed_seconds: float = 0.0

    def as_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "artifact_dir": str(self.artifact_dir),
            "base_model": self.base_model,
            "metrics": self.metrics,
            "calibration": self.calibration.as_dict(),
            "conformal": self.conformal.as_dict(),
            "baseline": self.baseline,
            "dev": self.dev_evaluation,
            "leakage": self.leakage,
            "data": {ref.name: ref.sha256 for ref in self.data_refs},
            "expected_problems": self.expected_problems,
            "config_sha256": config_fingerprint(self.config),
        }


# --------------------------------------------------------------------------- #
# The training loop                                                           #
# --------------------------------------------------------------------------- #


def _batches(size: int, batch_size: int, rng: Any) -> list[list[int]]:
    order = list(range(size))
    rng.shuffle(order)
    return [order[start : start + batch_size] for start in range(0, size, batch_size)]


def _loss_for_batch(
    torch: Any,
    model: Any,
    texts: Sequence[str],
    labels: Sequence[int],
    *,
    tokenizer: Any,
    max_length: int,
    device: str,
    matrix_rows: list[list[float]],
    label_smoothing: float,
    normalise_per_row: bool,
) -> Any:
    from manovia_ml.training.safety.cost import cost_weighted_cross_entropy

    encoded = tokenizer(
        list(texts),
        return_tensors="pt",
        truncation=True,
        max_length=max_length,
        padding=True,
    )
    encoded = {key: value.to(device) for key, value in encoded.items()}
    logits = model(**encoded).logits
    return cost_weighted_cross_entropy(
        logits,
        labels,
        torch=torch,
        matrix=matrix_rows,
        label_smoothing=label_smoothing,
        normalise_per_row=normalise_per_row,
    )


def _train_lora(
    *,
    config: TrainConfig,
    safety: SafetyRunConfig,
    model_config: SafetyLoraConfig,
    train_examples: Sequence[SafetyExample],
    dev_examples: Sequence[SafetyExample],
) -> tuple[Any, Any, list[dict[str, float]], int, str, str]:
    """Fit the LoRA model; return it, its tokenizer, the history and device info.

    The history rows stay numeric (one dict per epoch) so MLflow can chart them
    directly; the phase tag is set by :func:`train`, which owns the run.
    """
    import torch

    device = resolve_device(torch)
    build = build_lora_model(
        model_config.base_model,
        config=model_config.lora,
        labels=LEVELS,
    )
    model, tokenizer = build.model, build.tokenizer
    model.to(device)

    training = model_config.training
    matrix_rows = cost_matrix_rows()
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(
        trainable,
        lr=training.learning_rate,
        weight_decay=training.weight_decay,
    )
    rng = seeded_rng(config.seed, "train", "shuffle")
    total_steps = max(1, len(train_examples) // max(1, training.batch_size)) * training.epochs
    if training.max_steps is not None:
        total_steps = min(total_steps, training.max_steps)
    warmup = max(1, int(total_steps * training.warmup_ratio))

    def learning_rate(step: int) -> float:
        if step < warmup:
            return training.learning_rate * step / warmup
        progress = (step - warmup) / max(1, total_steps - warmup)
        if training.scheduler == "constant":
            return training.learning_rate
        if training.scheduler == "linear":
            return training.learning_rate * (1.0 - progress)
        import math

        return training.learning_rate * 0.5 * (1.0 + math.cos(math.pi * progress))

    history: list[dict[str, float]] = []
    best_score = -1.0
    best_state: dict[str, Any] | None = None
    stale = 0
    stopped_early = False
    best_epoch = 0
    global_step = 0
    started = time.monotonic()

    for epoch in range(1, training.epochs + 1):
        model.train()
        epoch_loss = 0.0
        epoch_batches = 0
        for batch in _batches(len(train_examples), training.batch_size, rng):
            if global_step >= total_steps:
                break
            for group in optimizer.param_groups:
                group["lr"] = learning_rate(global_step)
            texts = [train_examples[index].text for index in batch]
            labels = [LEVEL_INDEX[train_examples[index].label] for index in batch]
            loss = _loss_for_batch(
                torch,
                model,
                texts,
                labels,
                tokenizer=tokenizer,
                max_length=model_config.tokenizer_max_length,
                device=device,
                matrix_rows=matrix_rows,
                label_smoothing=safety.cost.label_smoothing,
                normalise_per_row=safety.cost.normalise_per_row,
            )
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(trainable, training.max_grad_norm)
            optimizer.step()
            epoch_loss += float(loss.detach().cpu())
            epoch_batches += 1
            global_step += 1

        dev_predictions, _dev_probabilities = _score(
            model, tokenizer, dev_examples, model_config, device, safety
        )
        dev_recall = _crisis_recall(dev_examples, dev_predictions)
        dev_macro = metric_fns.macro_f1(
            [example.label for example in dev_examples], dev_predictions
        )
        history.append(
            {
                "epoch": float(epoch),
                "train_loss": epoch_loss / max(1, epoch_batches),
                "dev_crisis_recall": dev_recall,
                "dev_macro_f1": dev_macro,
                "lr": float(optimizer.param_groups[0]["lr"]),
                "steps": float(global_step),
                "seconds": time.monotonic() - started,
            }
        )
        # Model selection is crisis recall first: this is the number the product
        # is graded on, and a cost-sensitive model that trades it away has failed
        # at the thing it was built for. Macro-F1 breaks ties.
        score = dev_recall + 1e-6 * dev_macro
        if score > best_score + 1e-9:
            best_score, best_epoch, stale = score, epoch, 0
            best_state = {
                name: parameter.detach().clone()
                for name, parameter in model.named_parameters()
                if parameter.requires_grad
            }
        else:
            stale += 1
        if training.early_stopping_patience and stale >= training.early_stopping_patience:
            stopped_early = True
            break
        if global_step >= total_steps:
            break

    if best_state is not None:
        with torch.no_grad():
            for name, parameter in model.named_parameters():
                if name in best_state:
                    parameter.copy_(best_state[name])

    return (
        model,
        tokenizer,
        history,
        best_epoch,
        device,
        ("stopped_early" if stopped_early else "completed"),
    )


def _score(
    model: Any,
    tokenizer: Any,
    examples: Sequence[SafetyExample],
    model_config: SafetyLoraConfig,
    device: str,
    safety: SafetyRunConfig,
    *,
    temperature: float = 1.0,
) -> tuple[list[str], list[dict[str, float]]]:
    """Point predictions (under the cost rule) and the probability maps."""
    from manovia_ml.training.safety.calibration import apply_temperature

    logits = predict_logits(
        model,
        tokenizer,
        examples,
        max_length=model_config.tokenizer_max_length,
        batch_size=model_config.training.eval_batch_size,
        device=device,
    )
    probabilities = [
        dict(zip(LEVELS, apply_temperature(row, temperature), strict=True)) for row in logits
    ]
    predictions = [
        metric_fns.top_level(row, cost_rule=safety.cost.cost_decision_rule) for row in probabilities
    ]
    return predictions, probabilities


def _crisis_recall(examples: Sequence[SafetyExample], predictions: Sequence[str]) -> float:
    truths = [example.label for example in examples]
    value = metric_fns.binary_crisis_metrics(truths, list(predictions))["recall"]
    return float(value) if value is not None else 0.0


# --------------------------------------------------------------------------- #
# The run                                                                     #
# --------------------------------------------------------------------------- #


def train(
    config: str | Path | TrainConfig,
    *,
    phase: str = "train",
    parent_run_id: str | None = None,
    log_model: bool = True,
    record_expected: bool = False,
    artifact_root: str | Path | None = None,
) -> SafetyTrainResult:
    """One safety run: fit, calibrate, conformalise, evaluate, log, report.

    ``config`` is a path to a YAML file, or an already-validated
    :class:`~manovia_ml.config.TrainConfig` (what the tests pass, so a test run
    writes into a temporary directory instead of ``ml/artifacts``).
    ``artifact_root`` overrides where the artefact lands, for the same reason.
    """
    started = time.monotonic()
    config_path: str | Path | None
    if isinstance(config, str | Path):
        config_path = config
        loaded = load_config(config)
    else:
        config_path = None
        loaded = config
    # Narrowed through a plain local: mypy's isinstance narrowing is reliable on a
    # local variable and unreliable across a member expression.
    model_config = loaded.model
    if loaded.safety is None or not isinstance(model_config, SafetyLoraConfig):
        raise ConfigError(
            "safety training needs model.kind 'safety_lora' and a `safety:` section; "
            "the TF-IDF baseline path is `safety_tfidf`"
        )
    config = loaded
    safety = loaded.safety
    if config.data.train is None or config.data.eval is None or config.data.validation is None:
        raise ConfigError("safety training needs train, eval and validation splits")
    seed_state = seed_everything(config.seed, pin_blas_threads=config.threads)

    train_examples = load_examples(config.data.train.path)
    dev_examples = load_examples(config.data.eval.path)
    calibration_examples = load_examples(config.data.validation.path)
    test_examples = load_examples(config.data.test.path) if config.data.test else []

    # --- The leakage guard, before a single optimiser step runs -------------- #
    # An identical row on both sides of the split is not a data property, it is a
    # bug: the model has already seen the answer. The emotion trainer refuses for
    # the same reason, and here the consequence is worse than a good-looking
    # number — it is a false sense of recall, in a domain where a false sense of
    # recall is the thing being designed against. Checked first so a corrupt
    # corpus costs a ConfigError, not a training run.
    leakage = near_duplicate_report(train_examples, test_examples)
    if leakage["exact_overlap"] > 0:
        raise ConfigError(
            f"{leakage['exact_overlap']} training rows appear verbatim in the held-out "
            "test split; rebuild the corpus before training (see `dvc repro safety_expand`)"
        )

    train_ref = dataset_ref("train", config.data.train.path)
    dev_ref = dataset_ref("dev", config.data.eval.path)
    calibration_ref = dataset_ref("calibration", config.data.validation.path)
    test_ref = dataset_ref("test", config.data.test.path) if config.data.test else None

    model, tokenizer, history, best_epoch, device, stop_reason = _train_lora(
        config=config,
        safety=safety,
        model_config=model_config,
        train_examples=train_examples,
        dev_examples=dev_examples,
    )

    dev_predictions, dev_probabilities = _score(
        model, tokenizer, dev_examples, model_config, device, safety
    )
    # --- Calibration: temperature scaling on the calibration split ------------ #
    # Raw logits, not the cost-rule predictions: fitting a temperature on the
    # probabilities a decision rule already reshaped would be fitting on a
    # downstream artefact, and choosing a level here would be tuning on the
    # calibration data.
    calibration_logits = predict_logits(
        model,
        tokenizer,
        calibration_examples,
        max_length=model_config.tokenizer_max_length,
        batch_size=model_config.training.eval_batch_size,
        device=device,
    )
    calibration_result = calibrate(
        calibration_logits,
        [example.label for example in calibration_examples],
        bins=safety.calibration.bins,
        split="calibration",
    )
    temperature = calibration_result.temperature if safety.calibration.enabled else 1.0

    # --- Conformal: the quantile comes from the same split -------------------- #
    scaled_probabilities = [
        dict(zip(LEVELS, _apply(temperature, row), strict=True)) for row in calibration_logits
    ]
    quantile, calibration_sets = calibrate_conformal(
        scaled_probabilities,
        [example.label for example in calibration_examples],
        alpha=safety.conformal.alpha,
    )
    calibration_coverage = set_coverage(
        calibration_sets, [example.label for example in calibration_examples]
    )

    # --- Held-out scoring on the frozen test split ---------------------------- #
    test_logits = predict_logits(
        model,
        tokenizer,
        test_examples,
        max_length=model_config.tokenizer_max_length,
        batch_size=model_config.training.eval_batch_size,
        device=device,
    )
    test_probabilities = [
        dict(zip(LEVELS, _apply(temperature, row), strict=True)) for row in test_logits
    ]
    test_predictions = [
        metric_fns.top_level(row, cost_rule=safety.cost.cost_decision_rule)
        for row in test_probabilities
    ]
    test_truths = [example.label for example in test_examples]
    test_sets = [prediction_set(row, quantile) for row in test_probabilities]
    test_abstained = [
        abstain_level(item, point=point)
        for item, point in zip(test_sets, test_predictions, strict=True)
    ]
    size_stats = set_size_stats(test_sets)
    conformal_result = ConformalResult(
        alpha=safety.conformal.alpha,
        quantile=quantile,
        calibration_rows=len(calibration_examples),
        calibration_coverage=calibration_coverage,
        target_coverage=1.0 - safety.conformal.alpha,
        heldout_rows=len(test_examples),
        heldout_coverage=set_coverage(test_sets, test_truths),
        abstention_rate=size_stats["abstention_rate"],
        mean_set_size=size_stats["mean_size"],
        raised_by_abstention=sum(
            1
            for point, final in zip(test_predictions, test_abstained, strict=True)
            if severity(final) > severity(point)
        ),
        evaluated_rows=len(test_examples),
        notes={"fitted_on": "calibration split", "policy": safety.conformal.policy},
    )

    dev_evaluation = _evaluate(
        truths=[example.label for example in dev_examples],
        predictions=dev_predictions,
        abstained=[
            abstain_level(item, point=point)
            for item, point in zip(
                [prediction_set(row, quantile) for row in dev_probabilities],
                dev_predictions,
                strict=True,
            )
        ],
        examples=dev_examples,
        probabilities=dev_probabilities,
        sets=[prediction_set(row, quantile) for row in dev_probabilities],
        safety=safety,
        seed=derive_seed(config.seed, "safety-eval", "dev"),
    )
    evaluation = _evaluate(
        truths=test_truths,
        predictions=test_predictions,
        abstained=test_abstained,
        examples=test_examples,
        probabilities=test_probabilities,
        sets=test_sets,
        safety=safety,
        seed=config.seed,
    )

    # --- The Day-9 TF-IDF baseline, on the same rows -------------------------- #
    baseline: dict[str, Any] = {}
    if safety.report.compare_baseline:
        baseline = _baseline_metrics(
            train_examples,
            calibration_examples,
            test_examples,
            class_weight=safety.baseline_class_weight,
            safety=safety,
            seed=config.seed,
        )

    artifact_dir = _save_artifact(
        model=model,
        tokenizer=tokenizer,
        config=config,
        safety=safety,
        calibration_result=calibration_result,
        conformal_result=conformal_result,
        evaluation=evaluation,
        baseline=baseline,
        dev_evaluation=dev_evaluation,
        history=history,
        device=device,
        base_model=model_config.base_model,
        artifact_root=artifact_root,
        best_epoch=best_epoch,
        stop_reason=stop_reason,
        data_refs=tuple(
            ref for ref in (train_ref, dev_ref, calibration_ref, test_ref) if ref is not None
        ),
        train_examples=train_examples,
    )

    metrics = _flat_metrics(evaluation)
    expected = load_expected_metrics(config_path) if config_path is not None else None
    problems: list[str] = []
    recorded_path: Path | None = None
    if expected is not None and expected.seed == config.seed:
        problems = compare_metrics(metrics, expected.metrics, tolerance=config.evaluation.tolerance)

    params = {
        "model.base_model": model_config.base_model,
        "model.tokenizer_max_length": model_config.tokenizer_max_length,
        "lora.rank": model_config.lora.rank,
        "lora.alpha": model_config.lora.alpha,
        "lora.dropout": model_config.lora.dropout,
        "lora.target_modules": list(model_config.lora.target_modules),
        "training.epochs": model_config.training.epochs,
        "training.batch_size": model_config.training.batch_size,
        "training.learning_rate": model_config.training.learning_rate,
        "training.max_steps": model_config.training.max_steps or 0,
        "training.best_epoch": best_epoch,
        "training.stop_reason": stop_reason,
        "training.label_smoothing": safety.cost.label_smoothing,
        "cost.matrix": "default",
        "cost.normalise_per_row": safety.cost.normalise_per_row,
        "cost.decision_rule": safety.cost.cost_decision_rule,
        "cost.class_weights": class_weights_from_cost(),
        "calibration.temperature": calibration_result.temperature,
        "calibration.ece_before": round(calibration_result.ece_before, 6),
        "calibration.ece_after": round(calibration_result.ece_after, 6),
        "conformal.alpha": safety.conformal.alpha,
        "conformal.quantile": quantile,
        "conformal.policy": safety.conformal.policy,
        "device": device,
        "torch.threads": config.threads,
        "labels": ",".join(LEVELS),
        "data.leakage.max_jaccard": leakage["max_jaccard"],
        "data.leakage.exact_overlap": leakage["exact_overlap"],
        **trainable_parameter_report(_build_report_view(model)),
    }

    tags_out = {
        "manovia.task": "safety",
        "manovia.base_model": model_config.base_model,
        "manovia.artifact_dir": str(artifact_dir),
        "manovia.calibration_split": "calibration",
        "manovia.conformal_split": "calibration",
        **(config.tracking.tags or {}),
    }
    data_refs = tuple(
        ref for ref in (train_ref, dev_ref, calibration_ref, test_ref) if ref is not None
    )
    with tracked_run(
        config,
        phase=phase,
        data_refs=data_refs,
        seed_state=seed_state,
        params=params,
        tags=tags_out,
        config_path=Path(config_path) if config_path is not None else None,
        parent_run_id=parent_run_id,
    ) as run:
        _stamp_run_id(artifact_dir, run.run_id)
        for entry in history:
            run.log_metrics(entry, step=int(entry["steps"]))
        run.log_metrics(metrics)
        run.log_dict(calibration_result.as_dict(), CALIBRATION_FILENAME)
        run.log_dict(conformal_result.as_dict(), CONFORMAL_FILENAME)
        run.log_dict(evaluation, "evaluation/test.json")
        run.log_dict(dev_evaluation, "evaluation/dev.json")
        run.log_dict(
            {
                "history": history,
                "cost_matrix": COST_MATRIX_DOC,
                "class_weights": class_weights_from_cost(),
            },
            "evaluation/history.json",
        )
        if baseline:
            run.log_dict(baseline, "evaluation/baseline.json")
        run.log_dict(leakage, "evaluation/leakage.json")
        run.log_dict(
            json.loads((artifact_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")),
            MANIFEST_FILENAME,
        )
        run.log_artifact(artifact_dir / CARD_FILENAME, artifact_path="card")
        if log_model:
            run.log_artifact(artifact_dir / MODEL_DIRNAME, artifact_path="model")
            run.log_artifact(artifact_dir / TOKENIZER_DIRNAME, artifact_path="tokenizer")
        if expected is not None:
            run.log_params({"expected.checked": "true", "expected.problems": len(problems)})
            if problems:
                run.set_tags({"manovia.expected": "failed"})
        run_id = run.run_id

    if record_expected and config_path is not None:
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
                    "Recorded by `make ml-record`. Every later run of this config with this "
                    "seed must match within `tolerance`."
                ),
            ),
        )

    return SafetyTrainResult(
        run_id=run_id,
        artifact_dir=artifact_dir,
        metrics=metrics,
        calibration=calibration_result,
        conformal=conformal_result,
        baseline=baseline,
        dev_evaluation=dev_evaluation,
        history=history,
        seed_state=seed_state,
        data_refs=data_refs,
        config=config,
        leakage=leakage,
        expected_problems=problems,
        expected_checked=expected is not None,
        recorded_expected_path=recorded_path,
        base_model=model_config.base_model,
        elapsed_seconds=time.monotonic() - started,
    )


def _apply(temperature: float, logits: Sequence[float]) -> list[float]:
    from manovia_ml.training.safety.calibration import apply_temperature

    return apply_temperature(logits, temperature)


def _build_report_view(model: Any) -> Any:
    """The parameter-count view the trainer logs (works for PEFT and plain models)."""
    return _ParameterView(model)


class _ParameterView:
    def __init__(self, model: Any) -> None:
        self._model = model

    @property
    def total_parameters(self) -> int:
        return sum(parameter.numel() for parameter in self._model.parameters())

    @property
    def trainable_parameters(self) -> int:
        return sum(
            parameter.numel() for parameter in self._model.parameters() if parameter.requires_grad
        )


# --------------------------------------------------------------------------- #
# Evaluation, baseline, artefact                                              #
# --------------------------------------------------------------------------- #


def _evaluate(
    *,
    truths: Sequence[str],
    predictions: Sequence[str],
    abstained: Sequence[str],
    examples: Sequence[SafetyExample],
    probabilities: Sequence[dict[str, float]],
    sets: Sequence[frozenset[str]],
    safety: SafetyRunConfig,
    seed: int,
) -> dict[str, Any]:
    """The held-out report: per-level, binary crisis, per-language, CIs, abstention."""
    # ECE on the held-out split, not on the calibration split the temperature was
    # fitted on: the two answer different questions and both belong in the report.
    heldout_ece = expected_calibration_error(probabilities, truths, bins=safety.calibration.bins)
    pairs = list(zip(truths, predictions, strict=True))
    abstain_pairs = list(zip(truths, abstained, strict=True))
    crisis = metric_fns.binary_crisis_metrics(truths, predictions)
    abstain_crisis = metric_fns.binary_crisis_metrics(truths, abstained)
    resamples = safety.report.bootstrap_resamples
    level = safety.report.confidence_level

    recall_ci = metric_fns.bootstrap_ci(
        pairs,
        lambda sample: metric_fns.binary_crisis_metrics(
            [item[0] for item in sample], [item[1] for item in sample]
        )["recall"],
        resamples=resamples,
        confidence_level=level,
        seed=derive_seed(seed, "safety-bootstrap", "recall"),
    )
    precision_ci = metric_fns.bootstrap_ci(
        pairs,
        lambda sample: metric_fns.binary_crisis_metrics(
            [item[0] for item in sample], [item[1] for item in sample]
        )["precision"],
        resamples=resamples,
        confidence_level=level,
        seed=derive_seed(seed, "safety-bootstrap", "precision"),
    )
    abstain_recall_ci = metric_fns.bootstrap_ci(
        abstain_pairs,
        lambda sample: metric_fns.binary_crisis_metrics(
            [item[0] for item in sample], [item[1] for item in sample]
        )["recall"],
        resamples=resamples,
        confidence_level=level,
        seed=derive_seed(seed, "safety-bootstrap", "abstain-recall"),
    )

    per_language: dict[str, dict[str, Any]] = {}
    for lang in sorted({example.lang for example in examples}):
        indices = [index for index, example in enumerate(examples) if example.lang == lang]
        if not indices:
            continue
        sub_truths = [truths[index] for index in indices]
        sub_predictions = [predictions[index] for index in indices]
        sub_abstained = [abstained[index] for index in indices]
        sub_crisis = metric_fns.binary_crisis_metrics(sub_truths, sub_predictions)
        sub_abstain = metric_fns.binary_crisis_metrics(sub_truths, sub_abstained)
        per_language[lang] = {
            "cases": len(indices),
            "crisis_cases": sub_crisis["crisis_support"],
            "crisis_recall": sub_crisis["recall"],
            "crisis_recall_ci": list(
                metric_fns.bootstrap_ci(
                    list(zip(sub_truths, sub_predictions, strict=True)),
                    lambda sample: metric_fns.binary_crisis_metrics(
                        [item[0] for item in sample], [item[1] for item in sample]
                    )["recall"],
                    resamples=resamples,
                    confidence_level=level,
                    seed=derive_seed(seed, "safety-bootstrap", "lang", lang),
                )
            ),
            "crisis_precision": sub_crisis["precision"],
            "abstention_recall": sub_abstain["recall"],
        }

    false_negatives = [
        {"id": examples[index].id, "true": truths[index], "predicted": predictions[index]}
        for index in range(len(truths))
        if truths[index] in metric_fns.CRISIS_LEVELS
        and predictions[index] not in metric_fns.CRISIS_LEVELS
    ]
    benign_escalations = sum(
        1
        for truth, prediction in pairs
        if truth not in metric_fns.CRISIS_LEVELS and prediction in metric_fns.CRISIS_LEVELS
    )

    return {
        "cases": len(truths),
        "accuracy": metric_fns.accuracy(truths, predictions),
        "macro_f1": metric_fns.macro_f1(truths, predictions),
        "crisis": crisis,
        "crisis_recall_ci": list(recall_ci),
        "crisis_precision_ci": list(precision_ci),
        "abstention_policy": {
            "crisis_recall": abstain_crisis["recall"],
            "crisis_recall_ci": list(abstain_recall_ci),
            "crisis_precision": abstain_crisis["precision"],
            "raised_by_abstention": sum(
                1
                for point, final in zip(predictions, abstained, strict=True)
                if severity(final) > severity(point)
            ),
        },
        "per_language": per_language,
        "per_level": metric_fns.per_level_prf(truths, predictions),
        "confusion_matrix": {
            "labels": list(LEVELS),
            "rows": metric_fns.confusion_matrix(truths, predictions),
        },
        "expected_cost": metric_fns.expected_cost(truths, predictions),
        "benign_escalations": benign_escalations,
        "false_negatives": false_negatives[: safety.report.error_cases],
        "false_negative_count": len(false_negatives),
        "set_sizes": set_size_stats(sets),
        "calibration_bins": DEFAULT_BINS,
        "heldout_ece": round(heldout_ece, 5),
    }


def _baseline_metrics(
    train_examples: Sequence[SafetyExample],
    calibration_examples: Sequence[SafetyExample],
    test_examples: Sequence[SafetyExample],
    *,
    class_weight: str,
    safety: SafetyRunConfig,
    seed: int,
) -> dict[str, Any]:
    """Fit the Day-9 TF-IDF architecture on the same rows and score it the same way."""
    from manovia_ml.training.safety.baselines import TfidfBaseline, fit_tfidf_baseline

    fit = fit_tfidf_baseline(
        train_examples,
        calibration_examples,
        class_weight=None if class_weight == "none" else class_weight,
    )
    scorer = TfidfBaseline(fit)
    probabilities = scorer.probabilities(test_examples)
    predictions = [
        metric_fns.top_level(row, cost_rule=safety.cost.cost_decision_rule) for row in probabilities
    ]
    truths = [example.label for example in test_examples]
    pairs = list(zip(truths, predictions, strict=True))
    crisis = metric_fns.binary_crisis_metrics(truths, predictions)
    return {
        "kind": "safety_tfidf",
        "train_rows": fit.train_rows,
        "crisis": crisis,
        "crisis_recall_ci": list(
            metric_fns.bootstrap_ci(
                pairs,
                lambda sample: metric_fns.binary_crisis_metrics(
                    [item[0] for item in sample], [item[1] for item in sample]
                )["recall"],
                resamples=safety.report.bootstrap_resamples,
                confidence_level=safety.report.confidence_level,
                seed=derive_seed(seed, "safety-bootstrap", "baseline"),
            )
        ),
        "accuracy": metric_fns.accuracy(truths, predictions),
        "macro_f1": metric_fns.macro_f1(truths, predictions),
        "expected_cost": metric_fns.expected_cost(truths, predictions),
        "ece": expected_calibration_error(probabilities, truths, bins=safety.calibration.bins),
        "class_weight": class_weight,
    }


def _flat_metrics(evaluation: dict[str, Any]) -> dict[str, float]:
    """The MLflow metric names: dotted, flat, all floats."""
    crisis = evaluation["crisis"]
    abstention = evaluation["abstention_policy"]
    out: dict[str, float] = {
        "accuracy": float(evaluation["accuracy"]),
        "macro_f1": float(evaluation["macro_f1"]),
        "crisis_recall": float(crisis["recall"] or 0.0),
        "crisis_precision": float(crisis["precision"] or 0.0),
        "crisis_f1": float(crisis["f1"] or 0.0),
        "crisis_false_negatives": float(crisis["false_negatives"]),
        "crisis_false_positives": float(crisis["false_positives"]),
        "expected_cost": float(evaluation["expected_cost"]),
        "abstention_crisis_recall": float(abstention["crisis_recall"] or 0.0),
        "abstention_raised": float(abstention["raised_by_abstention"]),
        "abstention_rate": float(evaluation["set_sizes"]["abstention_rate"]),
        "mean_set_size": float(evaluation["set_sizes"]["mean_size"]),
        "false_negative_count": float(evaluation["false_negative_count"]),
        "benign_escalations": float(evaluation["benign_escalations"]),
    }
    for label, value in evaluation["per_level"].items():
        if value["f1"] is not None:
            out[f"f1.{label}"] = float(value["f1"])
    return out


def _save_artifact(
    *,
    model: Any,
    tokenizer: Any,
    base_model: str,
    config: TrainConfig,
    safety: SafetyRunConfig,
    calibration_result: CalibrationResult,
    conformal_result: ConformalResult,
    evaluation: dict[str, Any],
    dev_evaluation: dict[str, Any],
    baseline: dict[str, Any],
    history: list[dict[str, float]],
    device: str,
    best_epoch: int,
    stop_reason: str,
    data_refs: tuple[DatasetRef, ...],
    train_examples: Sequence[SafetyExample],
    artifact_root: str | Path | None = None,
) -> Path:
    """Write the packaged artefact: merged model, tokenizer, calibration, card."""
    from manovia_ml.training.safety.model import SafetyLoRABuild
    from manovia_ml.training.safety.report import write_card

    root = Path(artifact_root) if artifact_root is not None else resolve(safety.artifact_dir)
    artifact_dir = root / safety.artifact_name
    artifact_dir.mkdir(parents=True, exist_ok=True)

    build = SafetyLoRABuild(
        model=model,
        tokenizer=tokenizer,
        labels=LEVELS,
        trainable_parameters=sum(
            parameter.numel() for parameter in model.parameters() if parameter.requires_grad
        ),
        total_parameters=sum(parameter.numel() for parameter in model.parameters()),
    )
    merge_and_save(build, artifact_dir)

    (artifact_dir / CALIBRATION_FILENAME).write_text(
        json.dumps(calibration_result.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (artifact_dir / CONFORMAL_FILENAME).write_text(
        json.dumps(conformal_result.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (artifact_dir / THRESHOLDS_FILENAME).write_text(
        json.dumps(
            {
                "decision_rule": "expected_cost" if safety.cost.cost_decision_rule else "argmax",
                "temperature": calibration_result.temperature,
                "conformal_quantile": conformal_result.quantile,
                "conformal_alpha": conformal_result.alpha,
                "abstention_policy": safety.conformal.policy,
                "labels": list(LEVELS),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "manifest_version": 1,
        "written": datetime.now(UTC).date().isoformat(),
        "task": "safety",
        "base_model": base_model,
        "labels": list(LEVELS),
        "data": {ref.name: ref.as_dict() for ref in data_refs},
        "seed": config.seed,
        "config_sha256": config_fingerprint(config),
        "device": device,
        "best_epoch": best_epoch,
        "stop_reason": stop_reason,
        "train_rows": len(train_examples),
        "calibration": calibration_result.as_dict(),
        "conformal": conformal_result.as_dict(),
        "evaluation": evaluation,
        "dev_evaluation": dev_evaluation,
        "baseline": baseline,
        "history": history,
    }
    (artifact_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_card(artifact_dir / CARD_FILENAME, config=config, safety=safety, manifest=manifest)
    return artifact_dir


def _stamp_run_id(artifact_dir: Path, run_id: str) -> None:
    """Record the MLflow run id inside the manifest so an artefact points home."""
    path = artifact_dir / MANIFEST_FILENAME
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["run_id"] = run_id
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _print_summary(result: SafetyTrainResult) -> None:
    crisis = result.metrics
    print(f"run_id={result.run_id}")
    print(f"artifact={result.artifact_dir}")
    print(f"base_model={result.base_model}  device/cpu-seconds={result.elapsed_seconds:.1f}")
    print(
        "HIGH+IMMINENT (test, frozen): "
        f"recall={crisis['crisis_recall']:.3f}  precision={crisis['crisis_precision']:.3f}  "
        f"macro_f1={crisis['macro_f1']:.3f}"
    )
    print(
        f"abstention policy: recall={crisis['abstention_crisis_recall']:.3f}  "
        f"raised={crisis['abstention_raised']:.0f}  abstention_rate={crisis['abstention_rate']:.3f}"
    )
    print(
        f"calibration: T={result.calibration.temperature:.3f}  "
        f"ECE {result.calibration.ece_before:.4f} -> {result.calibration.ece_after:.4f}"
    )
    print(
        f"conformal: target={result.conformal.target_coverage:.3f}  "
        f"calibration={result.conformal.calibration_coverage:.3f}  "
        f"held-out={result.conformal.heldout_coverage:.3f}  q={result.conformal.quantile:.4f}"
    )
    if result.baseline:
        print(
            f"baseline (tfidf, same rows): recall={result.baseline['crisis']['recall']:.3f}  "
            f"precision={result.baseline['crisis']['precision']:.3f}"
        )
    print(
        "leakage vs frozen test: "
        f"max_jaccard={result.leakage['max_jaccard']:.3f} "
        f"exact_overlap={result.leakage['exact_overlap']}"
    )
    if result.expected_checked:
        status = "FAIL" if result.expected_problems else "PASS"
        print(f"expected baseline: {status} ({len(result.expected_problems)} problems)")
        for problem in result.expected_problems:
            print(f"  {problem}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train the cost-sensitive safety classifier (Day 33)."
    )
    parser.add_argument("--config", required=True, help="path to the run config YAML")
    parser.add_argument("--phase", default="train", help="MLflow phase tag")
    parser.add_argument("--no-model", action="store_true", help="do not log the model artefact")
    parser.add_argument(
        "--record-expected", action="store_true", help="re-record this config's baseline metrics"
    )
    parser.add_argument("--json", action="store_true", help="print the machine-readable summary")
    args = parser.parse_args(argv)

    result = train(
        args.config,
        phase=args.phase,
        log_model=not args.no_model,
        record_expected=args.record_expected,
    )
    if args.json:
        print(json.dumps(result.as_json(), indent=2, sort_keys=True))
    else:
        _print_summary(result)
    if result.expected_problems:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
