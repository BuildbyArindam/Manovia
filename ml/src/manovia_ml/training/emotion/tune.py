"""A small Optuna sweep over LoRA rank and learning rate (≤ 12 trials).

Deliberately small, and deliberately two-dimensional. The brief caps the sweep at
12 trials; with a 95% interval roughly ±0.07 wide on the held-out set, a wider
search over more knobs would be fitting the validation split, not improving the
model. Rank and learning rate are the two that actually move a LoRA run:

* **rank** decides how much capacity the adapter has at all;
* **learning rate** decides whether that capacity is used or blown up in the
  first epoch — at rank 32 with 3e-3 the loss diverges, at rank 4 with 1e-5 it
  never leaves the base model's behaviour.

Each trial runs the *same* training function as a real run
(:func:`manovia_ml.training.emotion.train.train`), so a trial cannot be a
different code path from the run it is choosing for. Trials are nested MLflow
runs under a parent sweep run, which makes "which trial chose this rank" one
click rather than an archaeology exercise.

The objective is validation macro-F1 at default thresholds, not at tuned ones:
tuning thresholds inside the sweep would fit them to the split that is also
choosing the hyperparameters.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from manovia_ml.config import EmotionLoraConfig, TrainConfig, load_config
from manovia_ml.mlflow_api import mlflow
from manovia_ml.paths import default_tracking_uri
from manovia_ml.seeds import derive_seed
from manovia_ml.tracking import tracked_run

__all__ = ["SweepResult", "sweep"]


class SweepResult(dict):  # type: ignore[type-arg]
    """``dict`` subclass so the CLI and the tests read the same object.

    A plain dict would be enough, but a named type documents that the keys are
    part of this module's interface: ``trials``, ``best``, ``sweep_run_id``.
    """


def _trial_config(
    config: TrainConfig, *, rank: int, learning_rate: float, trial: int
) -> TrainConfig:
    model = config.model
    if not isinstance(model, EmotionLoraConfig):
        raise ValueError("the sweep only applies to model.kind 'emotion_lora'")
    lora = model.lora.model_copy(update={"rank": int(rank)})
    training = model.training.model_copy(update={"learning_rate": float(learning_rate)})
    tracking = config.tracking.model_copy(update={"run_name": f"{config.name}-trial{trial}"})
    return config.model_copy(
        update={
            "model": model.model_copy(update={"lora": lora, "training": training}),
            "tracking": tracking,
            "name": f"{config.name}_trial{trial}",
        }
    )


def sweep(
    config: TrainConfig,
    *,
    trials: int | None = None,
    config_path: str | None = None,
    work_dir: Path | None = None,
    tags: dict[str, str] | None = None,
) -> SweepResult:
    """Run the sweep and return the trials, the best setting and the run id."""

    model = config.model
    emotion = config.emotion
    if not isinstance(model, EmotionLoraConfig) or emotion is None:
        raise ValueError(
            "sweep() expects a model.kind 'emotion_lora' config with an `emotion:` section"
        )
    if not emotion.optuna.enabled:
        raise ValueError(
            "optuna.enabled is false: a sweep is a deliberate act, turn it on in the config"
        )
    import optuna

    from manovia_ml.training.emotion.train import train as train_once

    settings = emotion.optuna
    n_trials = trials if trials is not None else settings.n_trials
    n_trials = max(1, min(n_trials, 12))

    # Filled once the parent run is open; every trial is nested under it so the
    # sweep and its runs stay one tree in the MLflow UI.
    parent: dict[str, str] = {}

    def objective(trial: Any) -> float:

        rank = trial.suggest_categorical("rank", list(settings.rank_choices))
        learning_rate = trial.suggest_float(
            "learning_rate", settings.learning_rate_low, settings.learning_rate_high, log=True
        )
        trial_config = _trial_config(
            config, rank=rank, learning_rate=learning_rate, trial=trial.number
        )
        result = train_once(
            trial_config,
            config_path=config_path,
            phase="sweep",
            tags={**(tags or {}), "manovia.sweep": "true", "manovia.trial": str(trial.number)},
            work_dir=work_dir,
            parent_run_id=parent.get("run_id"),
        )
        score = (
            float(result.validation["macro_f1"])
            if settings.metric == "macro_f1"
            else -float(result.validation.get("ece", 0.0))
        )
        trial.set_user_attr("run_id", result.run_id)
        trial.set_user_attr("artifact_dir", str(result.artifact_dir))
        trial.set_user_attr("validation_macro_f1", float(result.validation["macro_f1"]))
        trial.set_user_attr(
            "test_macro_f1", float((result.test or {}).get("macro_f1", float("nan")))
        )
        return score

    # A seeded sampler plus the same config gives the same proposals; the trials
    # themselves are still stochastic (batching and dropout), which is why the
    # sweep records every trial rather than only the winner.
    sampler_seed = (
        settings.seed if settings.seed is not None else derive_seed(config.seed, "optuna")
    )
    sampler = optuna.samplers.TPESampler(seed=sampler_seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    mlflow.set_tracking_uri(config.tracking.tracking_uri or default_tracking_uri())
    mlflow.set_experiment(config.tracking.experiment)

    with tracked_run(
        config,
        phase="sweep",
        params={
            "optuna.n_trials": n_trials,
            "optuna.rank_choices": list(settings.rank_choices),
            "optuna.learning_rate_low": settings.learning_rate_low,
            "optuna.learning_rate_high": settings.learning_rate_high,
            "optuna.metric": settings.metric,
        },
        tags={"manovia.task": "emotion", "manovia.sweep": "true", **(tags or {})},
        config_path=Path(config_path) if config_path else None,
    ) as sweep_run:
        parent["run_id"] = sweep_run.run_id
        study.optimize(
            objective,
            n_trials=n_trials,
            timeout=settings.timeout_seconds,
            catch=(),
        )
        trial_records = [
            {
                "number": trial.number,
                "state": str(trial.state),
                "rank": trial.params.get("rank"),
                "learning_rate": trial.params.get("learning_rate"),
                "value": trial.value,
                "run_id": trial.user_attrs.get("run_id"),
                "validation_macro_f1": trial.user_attrs.get("validation_macro_f1"),
                "test_macro_f1": trial.user_attrs.get("test_macro_f1"),
                "artifact_dir": trial.user_attrs.get("artifact_dir"),
            }
            for trial in study.trials
        ]
        best = {
            "rank": study.best_params.get("rank"),
            "learning_rate": study.best_params.get("learning_rate"),
            "value": study.best_value,
        }
        sweep_run.log_metrics(
            {
                f"trial.{trial['number']}.value": float(trial["value"])
                for trial in trial_records
                if trial["value"] is not None
            }
        )
        sweep_run.log_params(
            {"sweep.best.rank": best["rank"], "sweep.best.learning_rate": best["learning_rate"]}
        )
        sweep_run.log_dict({"trials": trial_records, "best": best}, "sweep.json")
        return SweepResult(trials=trial_records, best=best, sweep_run_id=sweep_run.run_id)


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.training.emotion.tune",
        description="Small Optuna sweep over LoRA rank and learning rate.",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--trials", type=int, default=None, help="override optuna.n_trials (max 12)"
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    result = sweep(config, trials=args.trials, config_path=args.config)
    payload = dict(result)
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    else:
        print(f"wrote {args.out}; best={payload['best']}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(_main())
