"""LoRA fine-tuning for the multilingual emotion model, logged end to end.

The loop is small on purpose — it is a Hugging Face free, framework-light
training step written out rather than wrapped in ``Trainer``, because every
choice the brief asks for is then a line someone can read:

| requirement | where it is |
| --- | --- |
| BCE-with-logits | :func:`_loss_for_batch` (``problem_type`` keeps the head's own loss honest) |
| class weighting for rare labels | :func:`class_weights`, ``negatives / positives`` per label |
| label smoothing | ``training.label_smoothing``, applied to the soft targets |
| early stopping | :func:`train` (best adapter state restored, patience from config) |
| cosine schedule | :func:`_scheduler_for`, with a warmup ratio |
| gradient clipping | ``clip_grad_norm_`` at ``training.max_grad_norm`` |
| mixed precision | ``autocast`` + ``GradScaler`` **when a GPU exists**; fp32 on CPU |
| per-label thresholds | ``thresholds.tune_thresholds`` on the validation split |

What it will not do: train on a GPU that is not there. ``mixed_precision: auto``
resolves to fp16 on CUDA and to plain fp32 on CPU, and the resolved choice is
logged, because "we used mixed precision" is a claim about hardware.

Everything is logged to MLflow by ``manovia_ml.tracking`` before this module does
anything: git commit and dirty flag, the sha256 and row count of all three
splits, the resolved config and its fingerprint, the seed and its derived
sub-seeds, library versions (including torch/transformers/peft), the per-step
loss curve, per-epoch validation metrics, the tuned thresholds, the final
metrics on the held-out set and the artefact hashes.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from manovia_ml.config import (
    ConfigError,
    EmotionLoraConfig,
    ExpectedMetrics,
    TrainConfig,
    compare_metrics,
    config_fingerprint,
    load_config,
    load_expected_metrics,
    write_expected_metrics,
)
from manovia_ml.hashing import DatasetRef, check_expected, sha256_file
from manovia_ml.paths import ml_root, resolve
from manovia_ml.seeds import SeedState, derive_seed, seed_everything
from manovia_ml.tracking import tracked_run
from manovia_ml.training.emotion import metrics as metric_fns
from manovia_ml.training.emotion.data import (
    EmotionExample,
    example_dataset_ref,
    load_examples,
    overlap_report,
    summary,
)
from manovia_ml.training.emotion.evaluate import error_analysis, score_predictions
from manovia_ml.training.emotion.model import (
    CARD_FILENAME,
    MANIFEST_FILENAME,
    MODEL_DIRNAME,
    THRESHOLDS_FILENAME,
    TOKENIZER_DIRNAME,
    build_lora_model,
    merge_and_save,
    trainable_parameter_report,
)
from manovia_ml.training.emotion.taxonomy import EMOTIONS, NEUTRAL, SCHEMES, unreachable_targets
from manovia_ml.training.emotion.thresholds import tune_thresholds
from manovia_ml.training.emotion.tiny import (
    build_tiny_artefacts,
    is_tiny,
    tiny_factories,
)

__all__ = [
    "EmotionTrainResult",
    "class_weights",
    "main",
    "train",
]

#: ``negatives / positives`` above this is capped. A label with two positives in
#: 43k rows would otherwise get a weight in the thousands and the loss becomes a
#: prediction of that one row.
MAX_POS_WEIGHT = 50.0


@dataclass
class EmotionTrainResult:
    """Everything the CLI, the tests and the report need from one run."""

    run_id: str
    artifact_dir: Path
    metrics: dict[str, float]
    validation: dict[str, Any]
    test: dict[str, Any] | None
    thresholds: dict[str, float]
    history: list[dict[str, float]]
    seed_state: SeedState
    data_refs: tuple[DatasetRef, ...]
    config: TrainConfig
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
            "thresholds": self.thresholds,
            "validation_macro_f1": self.validation["macro_f1"],
            "validation_macro_f1_ci": self.validation["macro_f1_ci"],
            "test_macro_f1": (self.test or {}).get("macro_f1"),
            "test_macro_f1_ci": (self.test or {}).get("macro_f1_ci"),
            "per_language": (self.test or self.validation).get("per_language", {}),
            "data": {ref.name: ref.sha256 for ref in self.data_refs},
            "expected_problems": self.expected_problems,
            "config_sha256": config_fingerprint(self.config),
        }


def class_weights(
    examples: Sequence[EmotionExample],
    labels: Sequence[str] = EMOTIONS,
    *,
    cap: float = MAX_POS_WEIGHT,
) -> list[float]:
    """``pos_weight`` per label: negatives/positives, capped, never below 1.0.

    A label nobody can reach from the training scheme (``loneliness`` on
    GoEmotions) has no positives at all: its weight is 1.0 and the *report* says
    the label was untrainable, rather than a division by zero quietly turning the
    loss into NaN an hour later.
    """

    positives = [0] * len(labels)
    for example in examples:
        for index, label in enumerate(labels):
            if label in example.labels:
                positives[index] += 1
    total = len(examples)
    weights: list[float] = []
    for count in positives:
        if count == 0:
            weights.append(1.0)
            continue
        negatives = total - count
        weights.append(round(min(cap, max(1.0, negatives / count)), 6))
    return weights


def _smooth(targets: Sequence[Sequence[float]], smoothing: float) -> list[list[float]]:
    if smoothing <= 0.0:
        return [list(row) for row in targets]
    return [[value * (1.0 - smoothing) + smoothing / 2.0 for value in row] for row in targets]


def _resolve_base_model(
    config: EmotionLoraConfig,
    work_dir: Path,
    *,
    texts: Sequence[str],
    seed: int,
) -> tuple[str, Any | None, Any | None, dict[str, Any]]:
    """Resolve ``base_model`` to something loadable, building the tiny stand-in if asked.

    Returns ``(source, tokenizer_factory, model_factory, note)``. For a real hub
    id nothing is built here and both factories are ``None`` — the model is a
    network download the operator runs explicitly (AGENTS.md ground rule 10).
    """

    if not is_tiny(config.base_model):
        return config.base_model, None, None, {"kind": "hub-or-local", "source": config.base_model}

    directory = work_dir / "tiny-base-model"
    build_tiny_artefacts(directory, texts=list(texts), seed=seed)
    tokenizer_factory, model_factory = tiny_factories(directory)
    note = {
        "kind": "tiny-random",
        "source": config.base_model,
        "built_at": str(directory),
        "warning": (
            "Randomly initialised stand-in for a real checkpoint. Metrics from this run are "
            "plumbing checks, not emotion-recognition quality."
        ),
    }
    return str(directory), tokenizer_factory, model_factory, note


def _load_split(
    path: str | Path,
    *,
    name: str,
    expected_sha256: str | None,
    expected_rows: int | None,
) -> tuple[list[EmotionExample], DatasetRef]:
    resolved = resolve(path)
    examples = load_examples(resolved)
    ref = example_dataset_ref(resolved, examples, name=name)
    check_expected(ref, sha256=expected_sha256, rows=expected_rows)
    return examples, ref


def _batch_indices(size: int, batch_size: int, rng: Any) -> list[list[int]]:
    order = list(range(size))
    rng.shuffle(order)
    return [order[start : start + batch_size] for start in range(0, size, batch_size)]


def _loss_for_batch(
    logits: Any,
    targets: Any,
    *,
    pos_weight: Any,
    torch: Any,
) -> Any:
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight, reduction="mean")
    return loss_fn(logits, targets)


def _scheduler_for(optimizer: Any, total_steps: int, training: Any, torch: Any) -> Any:
    warmup = round(total_steps * training.warmup_ratio)
    warmup = max(1, min(warmup, max(1, total_steps))) if training.warmup_ratio > 0 else 0

    if training.scheduler == "constant":
        return torch.optim.lr_scheduler.LambdaLR(optimizer, lambda _step: 1.0)
    if training.scheduler == "linear":
        return torch.optim.lr_scheduler.LinearLR(
            optimizer, start_factor=1.0, end_factor=0.0, total_iters=max(1, total_steps)
        )

    def scale(step: int) -> float:
        if warmup and step < warmup:
            return (step + 1) / warmup
        progress = (step - warmup) / max(1, total_steps - warmup)
        progress = min(1.0, max(0.0, progress))
        return 0.5 * (1.0 + math.cos(math.pi * progress))

    return torch.optim.lr_scheduler.LambdaLR(optimizer, scale)


def train(
    config: TrainConfig,
    *,
    config_path: str | None = None,
    phase: str = "train",
    tags: dict[str, str] | None = None,
    record_expected: bool = False,
    log_model: bool = False,
    device: str | None = None,
    work_dir: Path | None = None,
    artifact_root: Path | None = None,
    parent_run_id: str | None = None,
) -> EmotionTrainResult:
    """Run one training job described by ``config`` and log it to MLflow."""

    if not isinstance(config.model, EmotionLoraConfig) or config.emotion is None:
        raise ConfigError("train() here expects a model.kind 'emotion_lora' config")
    import torch

    model_config = config.model
    emotion = config.emotion
    training = model_config.training
    labels = tuple(config.data.labels or EMOTIONS)
    seed_state = seed_everything(config.seed, pin_blas_threads=config.threads)
    started = time.monotonic()

    train_examples, train_ref = _load_split(
        config.data.train.path,
        name="train",
        expected_sha256=config.data.train.sha256,
        expected_rows=config.data.train.rows,
    )
    validation_examples, validation_ref = _load_split(
        (config.data.validation or config.data.eval).path,
        name="validation",
        expected_sha256=(config.data.validation or config.data.eval).sha256,
        expected_rows=(config.data.validation or config.data.eval).rows,
    )
    test_examples: list[EmotionExample] | None = None
    test_ref: DatasetRef | None = None
    if config.data.test is not None:
        test_examples, test_ref = _load_split(
            config.data.test.path,
            name="test",
            expected_sha256=config.data.test.sha256,
            expected_rows=config.data.test.rows,
        )

    # The held-out set has to *be* held out. A shared row inflates every number
    # below it, and no metric will ever show you that it happened.
    for name, held_out in (("validation", validation_examples), ("test", test_examples)):
        if held_out is None:
            continue
        report = overlap_report(train_examples, held_out)
        if not report.clean:
            raise ValueError(f"training data overlaps the {name} split\n{report.fail_message()}")

    work_directory = work_dir or (ml_root() / "artifacts" / "emotion" / config.name)
    work_directory.mkdir(parents=True, exist_ok=True)
    base_source, tokenizer_factory, model_factory, base_note = _resolve_base_model(
        model_config,
        work_directory,
        texts=[example.text for example in train_examples],
        seed=config.seed,
    )

    build = build_lora_model(
        base_source,
        config=model_config.lora,
        labels=labels,
        tokenizer_factory=tokenizer_factory,
        model_factory=model_factory,
        local_files_only=is_tiny(model_config.base_model),
    )
    model = build.model

    resolved_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    model.to(resolved_device)
    weights = class_weights(train_examples, labels)
    pos_weight = torch.tensor(weights, dtype=torch.float32, device=resolved_device)

    targets = _smooth(
        [[float(value) for value in row] for row in _target_matrix(train_examples, labels)],
        training.label_smoothing,
    )
    truths = _target_matrix(validation_examples, labels, soft=False)

    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(
        trainable, lr=training.learning_rate, weight_decay=training.weight_decay
    )

    batches_per_epoch = max(
        1, (len(train_examples) + training.batch_size - 1) // training.batch_size
    )
    planned_steps = batches_per_epoch * training.epochs
    total_steps = min(planned_steps, training.max_steps) if training.max_steps else planned_steps
    scheduler = _scheduler_for(optimizer, max(1, total_steps), training, torch)

    use_amp = resolved_device == "cuda" and training.mixed_precision != "off"
    amp_dtype = torch.float16 if training.mixed_precision in {"auto", "fp16"} else torch.bfloat16
    # via getattr: the attribute moved between torch versions, and the run only
    # needs it on CUDA (it is inert otherwise).
    scaler_factory = getattr(torch.amp, "GradScaler", None) or torch.cuda.amp.GradScaler
    scaler = scaler_factory("cuda", enabled=use_amp and amp_dtype is torch.float16)

    history: list[dict[str, float]] = []
    best_score = -1.0
    best_state: dict[str, Any] | None = None
    best_epoch = 0
    stale_epochs = 0
    global_step = 0
    stopped_early = False

    optimizer.zero_grad(set_to_none=True)
    for epoch in range(1, training.epochs + 1):
        rng = __import__("random").Random(derive_seed(config.seed, "emotion-batches", str(epoch)))
        model.train()
        epoch_loss = 0.0
        epoch_batches = 0
        for batch in _batch_indices(len(train_examples), training.batch_size, rng):
            texts = [train_examples[index].text for index in batch]
            encoded = build.tokenizer(
                texts,
                return_tensors="pt",
                truncation=True,
                max_length=model_config.tokenizer_max_length,
                padding=True,
            )
            encoded = {key: value.to(resolved_device) for key, value in encoded.items()}
            batch_targets = torch.tensor(
                [targets[index] for index in batch], dtype=torch.float32, device=resolved_device
            )

            if use_amp:
                with torch.autocast(device_type="cuda", dtype=amp_dtype):
                    logits = model(**encoded).logits
                    loss = _loss_for_batch(
                        logits, batch_targets, pos_weight=pos_weight, torch=torch
                    )
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(trainable, training.max_grad_norm)
                scaler.step(optimizer)
                scaler.update()
            else:
                logits = model(**encoded).logits
                loss = _loss_for_batch(logits, batch_targets, pos_weight=pos_weight, torch=torch)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(trainable, training.max_grad_norm)
                optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            scheduler.step()

            global_step += 1
            epoch_loss += float(loss.detach().cpu())
            epoch_batches += 1
            if global_step >= total_steps:
                break

        validation_probabilities = _probabilities(
            model,
            build.tokenizer,
            validation_examples,
            model_config.tokenizer_max_length,
            training.eval_batch_size,
            resolved_device,
        )
        val_rows = [
            (
                frozenset(example.labels),
                frozenset(label for index, label in enumerate(labels) if row[index] >= 0.5),
            )
            for example, row in zip(validation_examples, validation_probabilities, strict=True)
        ]
        val_macro, _ = metric_fns.macro_f1(val_rows, labels)
        entry = {
            "epoch": float(epoch),
            "train_loss": epoch_loss / max(1, epoch_batches),
            "val_macro_f1": val_macro,
            "lr": float(optimizer.param_groups[0]["lr"]),
            "steps": float(global_step),
        }
        history.append(entry)

        improved = val_macro > best_score + training.early_stopping_min_delta
        if improved:
            best_score = val_macro
            best_epoch = epoch
            stale_epochs = 0
            best_state = {
                name: parameter.detach().clone()
                for name, parameter in model.named_parameters()
                if parameter.requires_grad
            }
        else:
            stale_epochs += 1
        if training.early_stopping_patience and stale_epochs >= training.early_stopping_patience:
            stopped_early = True
            break
        if global_step >= total_steps:
            break

    if best_state is not None:
        with torch.no_grad():
            for name, parameter in model.named_parameters():
                if name in best_state:
                    parameter.copy_(best_state[name])

    validation_probabilities = _probabilities(
        model,
        build.tokenizer,
        validation_examples,
        model_config.tokenizer_max_length,
        training.eval_batch_size,
        resolved_device,
    )
    tuning = tune_thresholds(
        {
            label: [row[index] for row in validation_probabilities]
            for index, label in enumerate(labels)
        },
        {label: [bool(row[index]) for row in truths] for index, label in enumerate(labels)},
        config=emotion.thresholds,
        labels=labels,
        split="validation",
    )
    validation_scores = score_predictions(
        validation_examples,
        validation_probabilities,
        labels=labels,
        thresholds=tuning.thresholds,
        split="validation",
        bootstrap_resamples=emotion.report.bootstrap_resamples,
        confidence_level=emotion.report.confidence_level,
        calibration_bins=emotion.report.calibration_bins,
        seed=derive_seed(config.seed, "emotion-bootstrap", "validation"),
    )

    test_scores: dict[str, Any] | None = None
    error_cases: list[dict[str, Any]] = []
    if test_examples is not None:
        test_probabilities = _probabilities(
            model,
            build.tokenizer,
            test_examples,
            model_config.tokenizer_max_length,
            training.eval_batch_size,
            resolved_device,
        )
        test_scores = score_predictions(
            test_examples,
            test_probabilities,
            labels=labels,
            thresholds=tuning.thresholds,
            split="test",
            bootstrap_resamples=emotion.report.bootstrap_resamples,
            confidence_level=emotion.report.confidence_level,
            calibration_bins=emotion.report.calibration_bins,
            seed=derive_seed(config.seed, "emotion-bootstrap", "test"),
        )
        error_cases = [
            case.as_dict()
            for case in error_analysis(
                test_examples,
                test_probabilities,
                labels=labels,
                thresholds=tuning.thresholds,
                trained_languages=sorted({example.lang for example in train_examples}),
                limit=emotion.report.error_cases,
            )
        ]

    artifact_dir = _save_artifacts(
        build=build,
        labels=labels,
        thresholds=tuning,
        config=config,
        base_note=base_note,
        history=history,
        validation=validation_scores,
        test=test_scores,
        train_summary=summary(train_examples),
        validation_summary=summary(validation_examples),
        test_summary=summary(test_examples) if test_examples else None,
        base_model=model_config.base_model,
        device=resolved_device,
        mixed_precision=(
            "fp16" if use_amp and amp_dtype is torch.float16 else "bf16" if use_amp else "off"
        ),
        stopped_early=stopped_early,
        best_epoch=best_epoch,
        artifact_dir=emotion.artifact_dir,
        artifact_name=emotion.artifact_name,
        artifact_root=artifact_root,
    )

    metrics = _flat_metrics(validation_scores, test_scores)
    expected = load_expected_metrics(config_path) if config_path else None
    problems: list[str] = []
    recorded_path: Path | None = None
    if expected is not None and expected.seed == config.seed:
        problems = compare_metrics(metrics, expected.metrics, tolerance=config.evaluation.tolerance)

    params = {
        "model.base_model": model_config.base_model,
        "model.base_note": base_note["kind"],
        "lora.rank": model_config.lora.rank,
        "lora.alpha": model_config.lora.alpha,
        "lora.dropout": model_config.lora.dropout,
        "lora.target_modules": list(model_config.lora.target_modules),
        "lora.freeze_base": model_config.lora.freeze_base,
        "training.epochs": training.epochs,
        "training.batch_size": training.batch_size,
        "training.learning_rate": training.learning_rate,
        "training.weight_decay": training.weight_decay,
        "training.warmup_ratio": training.warmup_ratio,
        "training.max_grad_norm": training.max_grad_norm,
        "training.label_smoothing": training.label_smoothing,
        "training.class_weighting": training.class_weighting,
        "training.scheduler": training.scheduler,
        "training.mixed_precision": (
            "fp16" if use_amp and amp_dtype is torch.float16 else "bf16" if use_amp else "off"
        ),
        "training.mixed_precision.requested": training.mixed_precision,
        "training.max_steps": total_steps,
        "training.early_stopping_patience": training.early_stopping_patience,
        "training.stopped_early": stopped_early,
        "training.best_epoch": best_epoch,
        "device": resolved_device,
        "torch.threads": config.threads,
        "labels": ",".join(labels),
        "labels_unreachable_from_training_data": ",".join(
            label for label in labels if summary(train_examples)["labels"][label] == 0
        ),
        "class_weights.pos_weight": [round(value, 4) for value in weights],
        "thresholds.tuned_on": "validation",
        "thresholds.values": {label: round(value, 4) for label, value in tuning.thresholds.items()},
        "thresholds.default": emotion.thresholds.default,
        "data.overlap.check": "clean",
        **trainable_parameter_report(build),
    }

    tags_out = {
        "manovia.task": "emotion",
        "manovia.base_model": model_config.base_model,
        "manovia.artifact_dir": str(artifact_dir),
        "manovia.threshold_split": "validation",
        **(tags or {}),
    }
    data_refs = tuple(ref for ref in (train_ref, validation_ref, test_ref) if ref is not None)
    with tracked_run(
        config,
        phase=phase,
        data_refs=data_refs,
        seed_state=seed_state,
        params=params,
        tags=tags_out,
        config_path=Path(config_path) if config_path else None,
        parent_run_id=parent_run_id,
    ) as run:
        for entry in history:
            run.log_metrics(entry, step=int(entry["steps"]))
        run.log_metrics(metrics)
        run.log_metrics(
            {
                f"f1.{label}": value
                for label, value in (test_scores or validation_scores)["per_label_f1"].items()
                if value is not None
            }
        )
        run.log_dict(validation_scores, "evaluation/validation.json")
        if test_scores is not None:
            run.log_dict(test_scores, "evaluation/test.json")
        run.log_dict({"history": history, "class_weights": weights}, "evaluation/history.json")
        run.log_dict(tuning.as_dict(), f"evaluation/{THRESHOLDS_FILENAME}")
        run.log_dict(
            {
                "errors": error_cases,
                "note": "heuristic grouping; sorted by |p - y| on the row's worst label",
            },
            "evaluation/errors.json",
        )
        run.log_dict(
            json.loads((artifact_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")),
            MANIFEST_FILENAME,
        )
        run.log_artifact(artifact_dir / CARD_FILENAME, artifact_path="card")
        run.log_artifact(artifact_dir / THRESHOLDS_FILENAME, artifact_path="thresholds")
        if log_model:
            run.log_artifact(artifact_dir / MODEL_DIRNAME, artifact_path="model")
            run.log_artifact(artifact_dir / TOKENIZER_DIRNAME, artifact_path="tokenizer")
        if expected is not None:
            run.log_params({"expected.checked": "true", "expected.problems": len(problems)})
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

    return EmotionTrainResult(
        run_id=run_id,
        artifact_dir=artifact_dir,
        metrics=metrics,
        validation=validation_scores,
        test=test_scores,
        thresholds=tuning.thresholds,
        history=history,
        seed_state=seed_state,
        data_refs=data_refs,
        config=config,
        expected_problems=problems,
        expected_checked=expected is not None,
        recorded_expected_path=recorded_path,
        base_model=model_config.base_model,
        elapsed_seconds=time.monotonic() - started,
    )


def _target_matrix(
    examples: Sequence[EmotionExample],
    labels: Sequence[str],
    *,
    soft: bool = True,
) -> list[list[float]]:
    matrix: list[list[float]] = []
    for example in examples:
        weights = example.weight_map()
        row: list[float] = []
        for label in labels:
            if soft and label in weights:
                row.append(round(float(weights[label]), 6))
            else:
                row.append(1.0 if label in example.labels else 0.0)
        if soft and not any(row):
            row[labels.index(NEUTRAL)] = 1.0
        matrix.append(row)
    return matrix


def _probabilities(
    model: Any,
    tokenizer: Any,
    examples: Sequence[EmotionExample],
    max_length: int,
    batch_size: int,
    device: str,
) -> list[list[float]]:
    from manovia_ml.training.emotion.inference import predict_probabilities

    return predict_probabilities(
        model,
        tokenizer,
        [example.text for example in examples],
        max_length=max_length,
        batch_size=batch_size,
        device=device,
    )


def _flat_metrics(validation: dict[str, Any], test: dict[str, Any] | None) -> dict[str, float]:
    """The scalars the committed baseline checks and the report quotes."""

    metrics = {
        "validation.macro_f1": float(validation["macro_f1"]),
        "validation.micro_f1": float(validation["micro_f1"]),
        "validation.subset_accuracy": float(validation["subset_accuracy"]),
        "validation.ece": float(validation["ece"]),
    }
    if test is not None:
        metrics |= {
            "test.macro_f1": float(test["macro_f1"]),
            "test.micro_f1": float(test["micro_f1"]),
            "test.subset_accuracy": float(test["subset_accuracy"]),
            "test.ece": float(test["ece"]),
        }
    return metrics


def _save_artifacts(
    *,
    build: Any,
    labels: Sequence[str],
    thresholds: Any,
    config: TrainConfig,
    base_note: dict[str, Any],
    history: Sequence[dict[str, float]],
    validation: dict[str, Any],
    test: dict[str, Any] | None,
    train_summary: dict[str, Any],
    validation_summary: dict[str, Any],
    test_summary: dict[str, Any] | None,
    base_model: str,
    device: str,
    mixed_precision: str,
    stopped_early: bool,
    best_epoch: int,
    artifact_dir: str,
    artifact_name: str,
    artifact_root: Path | None,
) -> Path:
    """Write the packaged artefact: model, tokenizer, thresholds, manifest, card.

    ``artifact_dir`` is relative to the ML root unless it is absolute, and
    ``artifact_root`` overrides the ML root — that is what lets the test suite
    train a real artefact without writing into the repository.
    """

    from manovia_ml.training.emotion.report import write_model_card

    base = Path(artifact_dir)
    if not base.is_absolute():
        base = (artifact_root or ml_root()) / base
    root = (base / artifact_name).resolve()
    root.mkdir(parents=True, exist_ok=True)
    merged = merge_and_save(build, root / MODEL_DIRNAME)
    build.tokenizer.save_pretrained(str(root / TOKENIZER_DIRNAME))

    (root / THRESHOLDS_FILENAME).write_text(
        json.dumps(thresholds.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    model_files = sorted(path for path in merged.rglob("*") if path.is_file())
    assert isinstance(config.model, EmotionLoraConfig)  # _save_artifacts is emotion-only
    manifest = {
        "manifest_version": 1,
        "kind": "manovia-emotion-lora",
        "labels": list(labels),
        "base_model": base_model,
        "base_note": base_note,
        "device": device,
        "mixed_precision": mixed_precision,
        "stopped_early": stopped_early,
        "best_epoch": best_epoch,
        "config": config.name,
        "config_sha256": config_fingerprint(config),
        "seed": config.seed,
        "lora": {
            "rank": config.model.lora.rank,
            "alpha": config.model.lora.alpha,
            "dropout": config.model.lora.dropout,
            "target_modules": list(config.model.lora.target_modules),
        },
        "thresholds": {label: round(value, 4) for label, value in thresholds.thresholds.items()},
        "metrics": _flat_metrics(validation, test),
        "data": {
            "train": train_summary,
            "validation": validation_summary,
            "test": test_summary,
        },
        "unreachable_labels": list(unreachable_targets(SCHEMES["goemotions"])),
        "history": list(history),
        "model_files": [
            {"name": str(path.relative_to(root)), "sha256": sha256_file(path)}
            for path in model_files
        ],
        "written": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (root / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_model_card(
        root / CARD_FILENAME,
        manifest=manifest,
        validation=validation,
        test=test,
        thresholds=thresholds,
    )
    return root


def _print_summary(result: EmotionTrainResult) -> None:
    print(f"RUN_ID={result.run_id}")
    print(
        f"config={result.config.name} base_model={result.base_model} seed={result.config.seed} "
        f"artifact_dir={result.artifact_dir}"
    )
    for ref in result.data_refs:
        print(f"data {ref.name}: {ref.rows} rows sha256={ref.sha256[:12]}…")
    print(
        "validation: macro_f1={:.4f} ci={}".format(
            result.validation["macro_f1"], result.validation["macro_f1_ci"]
        )
    )
    if result.test is not None:
        print(
            "test:       macro_f1={:.4f} ci={}".format(
                result.test["macro_f1"], result.test["macro_f1_ci"]
            )
        )
        for language, values in sorted(result.test["per_language"].items()):
            print(f"  {language}: macro_f1={values['macro_f1']:.4f} rows={values['rows']}")
    print(
        "thresholds: "
        + " ".join(f"{label}={value:.2f}" for label, value in sorted(result.thresholds.items()))
    )
    if result.recorded_expected_path:
        print(f"baseline recorded: {result.recorded_expected_path}")
    if result.expected_problems:
        print("baseline drift:", file=sys.stderr)
        for problem in result.expected_problems:
            print(f"  - {problem}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.training.emotion.train",
        description="LoRA fine-tune the multilingual emotion model and log the run.",
    )
    parser.add_argument("--config", required=True, help="e.g. configs/emotion_lora_smoke.yaml")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--phase", default="train")
    parser.add_argument("--record-expected", action="store_true")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--device", default=None)
    parser.add_argument(
        "--log-model",
        action="store_true",
        help="upload the merged weights to the tracking store (off by default: they are large)",
    )
    parser.add_argument("--json", action="store_true")
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
        log_model=args.log_model,
        device=args.device,
    )
    if args.json:
        print(json.dumps(result.as_json(), indent=2, sort_keys=True))
    else:
        _print_summary(result)
    return 1 if result.expected_problems else 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
