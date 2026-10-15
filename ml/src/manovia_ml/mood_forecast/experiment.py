"""Run and log the Day-36 baseline + optional sequence-model comparison."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np

from manovia_ml.hashing import DatasetRef
from manovia_ml.mood_forecast.baselines import (
    BASELINE_NAMES,
    BaselineName,
    baseline_quantiles,
    predict_baseline,
)
from manovia_ml.mood_forecast.config import MoodForecastConfig
from manovia_ml.mood_forecast.data import (
    FEATURE_NAMES,
    MoodWindows,
    WindowSample,
    ablate_features,
    build_mood_windows,
)
from manovia_ml.mood_forecast.metrics import (
    apply_interval_calibrator,
    evaluate_predictions,
    fit_interval_calibrator,
)
from manovia_ml.mood_forecast.models import (
    TorchUnavailableError,
    fit_sequence_model,
    predict_quantiles,
    torch_available,
)
from manovia_ml.mood_forecast.personalization import personalize_last_layer
from manovia_ml.mood_forecast.synthetic import (
    dataset_sha256,
    generate_synthetic_panel,
    panel_manifest,
)
from manovia_ml.paths import resolve
from manovia_ml.seeds import seed_everything
from manovia_ml.tracking import tracked_run


def _baseline_predictions(
    samples: tuple[WindowSample, ...], kind: BaselineName, smoothing_alpha: float
) -> np.ndarray:
    if not samples:
        return np.empty((0, 3, 3), dtype=np.float64)
    return cast(
        np.ndarray,
        np.stack(
            [
                baseline_quantiles(predict_baseline(sample, kind, smoothing_alpha=smoothing_alpha))
                for sample in samples
            ]
        ),
    )


def _as_json(value: Any) -> Any:
    """Convert nested NumPy scalars without ever serialising panel rows."""
    if isinstance(value, dict):
        return {str(key): _as_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_json(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def _score_baselines(
    windows: MoodWindows, config: MoodForecastConfig
) -> tuple[dict[str, dict[str, Any]], dict[str, np.ndarray]]:
    scores: dict[str, dict[str, Any]] = {}
    test_predictions: dict[str, np.ndarray] = {}
    for name in BASELINE_NAMES:
        calibration_raw = _baseline_predictions(
            windows.calibration, name, config.evaluation.smoothing_alpha
        )
        calibrator = fit_interval_calibrator(
            windows.calibration,
            calibration_raw,
            nominal_coverage=config.evaluation.nominal_coverage,
        )
        test_raw = _baseline_predictions(windows.test, name, config.evaluation.smoothing_alpha)
        calibrated = apply_interval_calibrator(test_raw, calibrator)
        scores[name] = {
            **evaluate_predictions(
                windows.test, calibrated, nominal_coverage=config.evaluation.nominal_coverage
            ),
            "calibration_corrections": list(calibrator.corrections),
            "calibration_counts": list(calibrator.calibration_counts),
        }
        test_predictions[name] = calibrated
    return scores, test_predictions


def _support_and_guard(
    samples: tuple[WindowSample, ...], *, minimum_support: int
) -> tuple[list[WindowSample], list[WindowSample]]:
    """Split an individual's earlier support era into ordered fit and guard rows."""
    ordered = sorted(samples, key=lambda sample: sample.origin_index)
    if len(ordered) <= minimum_support + 3:
        return [], []
    support_end = max(minimum_support, int(len(ordered) * 0.6))
    support = ordered[:support_end]
    last_support_origin = max(sample.origin_index for sample in support)
    # Keep a target gap so no guard target is an outcome used for fitting.
    guard = [
        sample
        for sample in ordered[support_end:]
        if sample.origin_index + 1 > last_support_origin + 3
    ]
    return support, guard


def _few_shot_evaluation(
    model: Any,
    windows: MoodWindows,
    base_predictions: np.ndarray,
    config: MoodForecastConfig,
) -> dict[str, Any]:
    """Adapt held-out users chronologically and gate on a later, untouched block."""
    from manovia_ml.mood_forecast.models import predict_quantiles

    predictions = np.array(base_predictions, dtype=np.float64, copy=True)
    test_indices: dict[str, list[int]] = {}
    for index, sample in enumerate(windows.test):
        test_indices.setdefault(sample.user_id, []).append(index)
    support_by_user: dict[str, list[WindowSample]] = {}
    for sample in windows.personalization_support:
        support_by_user.setdefault(sample.user_id, []).append(sample)

    user_results: list[dict[str, Any]] = []
    base_errors: list[float] = []
    personalised_errors: list[float] = []
    for user_id, indices in sorted(test_indices.items()):
        samples = [windows.test[index] for index in indices]
        support, guard = _support_and_guard(
            tuple(support_by_user.get(user_id, ())),
            minimum_support=config.training.few_shot_min_support,
        )
        if not support or not guard:
            user_results.append(
                {"user_id": user_id, "accepted": False, "reason": "insufficient_support_or_guard"}
            )
            continue
        candidate = copy.deepcopy(model)
        result = personalize_last_layer(
            candidate,
            support,
            guard,
            minimum_support=config.training.few_shot_min_support,
            steps=config.training.few_shot_steps,
            learning_rate=config.training.few_shot_learning_rate,
            l2=config.training.few_shot_l2,
            max_update_norm=config.training.few_shot_max_update_norm,
            max_validation_loss_increase=config.training.few_shot_max_validation_loss_increase,
        )
        user_summary = {
            "user_id": user_id,
            "accepted": result.accepted,
            "reason": result.reason,
            "support_windows": result.support_windows,
            "validation_pinball_before": result.validation_pinball_before,
            "validation_pinball_after": result.validation_pinball_after,
            "update_norm": result.update_norm,
        }
        user_results.append(user_summary)
        if not result.accepted:
            continue
        user_predictions = predict_quantiles(candidate, samples)
        for local_index, global_index in enumerate(indices):
            predictions[global_index] = user_predictions[local_index]

    for index, sample in enumerate(windows.test):
        if not sample.target_mask.any():
            continue
        actual = sample.targets[sample.target_mask]
        base_errors.extend(np.abs(base_predictions[index, sample.target_mask, 1] - actual).tolist())
        personalised_errors.extend(
            np.abs(predictions[index, sample.target_mask, 1] - actual).tolist()
        )
    attempted = [row for row in user_results if row["reason"] != "insufficient_support_or_guard"]
    return {
        "users": user_results,
        "attempted_users": len(attempted),
        "accepted_users": sum(bool(row["accepted"]) for row in user_results),
        "accepted_fraction": (
            sum(bool(row["accepted"]) for row in user_results) / len(attempted)
            if attempted
            else None
        ),
        "base_test_mae": float(np.mean(base_errors)) if base_errors else None,
        "personalised_test_mae": float(np.mean(personalised_errors))
        if personalised_errors
        else None,
        "note": (
            "Test outcomes were used only after adaptation for scoring. The later guard block "
            "could reject an update but was never optimised on. Intervals are not claimed for "
            "personalised forecasts."
        ),
    }


def _render_report(
    *,
    config: MoodForecastConfig,
    summary: dict[str, Any],
    run_id: str,
    manifest: dict[str, Any],
) -> str:
    lines = [
        "# Day 36 — Mood forecasting with sequence models and honest uncertainty",
        "",
        f"Research run `{run_id}`; generated {datetime.now(UTC).date().isoformat()} from "
        f"config `{config.name}` (seed `{config.seed}`).",
        "",
        "> **Research only.** The panel is synthetic; these numbers do not describe Manovia users. "
        "No trained weights are served by the product. The user-facing Outlook uses a separate, "
        "deterministic personal-history baseline and remains opt-in.",
        "",
        "## Data and split",
        "",
        f"- Generator: `{manifest['generator_version']}`; SHA-256: `{manifest['sha256']}`.",
        f"- {manifest['users']} synthetic users; {manifest['calendar_days_per_user']} calendar "
        f"days each; {manifest['logged_days']} observed check-ins across {manifest['daily_rows']} "
        "calendar rows. Missing outcomes were masked, not imputed.",
        f"- Training users: {summary['split']['train_user_count']}; calibration users: "
        f"{summary['split']['calibration_user_count']}; held-out test users: "
        f"{summary['split']['test_user_count']}. User groups are disjoint; target windows are "
        "forward-only and remain within each temporal segment.",
        "- Temporal boundaries (calendar-day indices; start-inclusive, end-exclusive): "
        f"fit targets [0, {summary['split']['fit_cut']}); "
        f"validation targets [{summary['split']['fit_cut']}, {summary['split']['train_cut']}); "
        f"calibration targets [{summary['split']['train_cut']}, "
        f"{summary['split']['calibration_cut']}); held-out test targets start at "
        f"{summary['split']['calibration_cut']}.",
        "- Inputs: last 28 daily values/missingness, valence, energy, sleep tag, exercise, closed "
        "factor tags and weekday; outputs: next three daily valence 10/50/90 quantiles.",
        "",
        "## Baseline and sequence-model comparison",
        "",
        f"Intervals were calibrated at {config.evaluation.nominal_coverage:.0%} on the separate "
        "calibration-user group using horizon-specific split-conformal expansion. Coverage and "
        "width below are measured on the untouched held-out users.",
        "",
        (
            "| model | MAE ↓ | MASE vs seasonal naive ↓ | actual / nominal coverage | "
            "interval width | pinball loss ↓ | test targets |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, metrics in summary["scores"].items():
        lines.append(
            f"| `{name}` | {metrics['mae']:.3f} | {metrics['mase_vs_seasonal_naive']:.3f} | "
            f"{metrics['interval_coverage']:.1%} / {metrics['nominal_coverage']:.0%} | "
            f"{metrics['interval_width']:.3f} | {metrics['pinball_loss']:.3f} | "
            f"{int(metrics['observed_targets'])} |"
        )
    if summary["neural_status"] == "available":
        for name, metrics in summary["neural_scores"].items():
            lines.append(
                f"| `{name}` | {metrics['mae']:.3f} | {metrics['mase_vs_seasonal_naive']:.3f} | "
                f"{metrics['interval_coverage']:.1%} / {metrics['nominal_coverage']:.0%} | "
                f"{metrics['interval_width']:.3f} | {metrics['pinball_loss']:.3f} | "
                f"{int(metrics['observed_targets'])} |"
            )
        best_neural = min(
            summary["neural_scores"], key=lambda name: summary["neural_scores"][name]["mae"]
        )
        baseline_name = summary["best_baseline"]
        baseline_mae = summary["scores"][baseline_name]["mae"]
        neural_mae = summary["neural_scores"][best_neural]["mae"]
        verdict = "beat" if neural_mae < baseline_mae else "did not beat"
        lines.extend(
            [
                "",
                f"**Result:** the best neural model (`{best_neural}`, MAE {neural_mae:.3f}) "
                f"{verdict} the best baseline (`{baseline_name}`, MAE {baseline_mae:.3f}) "
                "on held-out synthetic users. This is a synthetic benchmark, not a "
                "product-quality claim.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "**Neural result: not measured.** The configured GRU, LSTM and Transformer were "
                "not trained because PyTorch was unavailable. No claim that a neural model beats "
                "the baseline is possible from this run.",
            ]
        )
    lines.extend(
        [
            "",
            "### Nominal vs actual interval coverage",
            "",
            f"Nominal marginal coverage is {config.evaluation.nominal_coverage:.0%}; see each "
            "table row for held-out actual coverage and mean interval width. Per-horizon values "
            "are recorded in the MLflow summary artifact. Coverage is an empirical result on this "
            "simulator, not a guarantee for an individual.",
            "",
            "## Feature ablation",
            "",
        ]
    )
    if summary["ablation"]:
        lines.extend(
            [
                "| model | ablated group | MAE | change vs full model |",
                "| --- | --- | ---: | ---: |",
            ]
        )
        for model_name, groups in summary["ablation"].items():
            for group_name, metrics in groups.items():
                delta = metrics["mae"] - summary["neural_scores"][model_name]["mae"]
                lines.append(
                    f"| `{model_name}` | `{group_name}` | {metrics['mae']:.3f} | {delta:+.3f} |"
                )
    else:
        lines.append(
            "Not evaluated: PyTorch is unavailable, so neural feature ablations could not run. "
            "Baselines do not consume those feature groups; their values would not be an "
            "informative ablation."
        )
    lines.extend(["", "## Few-shot last-layer personalisation", ""])
    if summary["personalization"]:
        for model_name, few_shot in summary["personalization"].items():
            lines.extend(
                [
                    f"### `{model_name}`",
                    "",
                    f"Accepted {few_shot['accepted_users']} of {few_shot['attempted_users']} "
                    "attempted held-out-user updates. Updates affected only the regularised, "
                    "norm-bounded final linear layer and were reverted when a later guard block "
                    "exceeded the loss tolerance.",
                    f"Base test MAE: {few_shot['base_test_mae']:.3f}; few-shot test MAE: "
                    f"{few_shot['personalised_test_mae']:.3f}. {few_shot['note']}",
                    "",
                ]
            )
    else:
        lines.append(
            "Not evaluated: neural weights were unavailable. The update code and optional "
            "tests are included."
        )
    lines.extend(
        [
            "",
            "## Cold-start serving policy (separate from this research model)",
            "",
            "The product Outlook requires explicit opt-in, at least 14 distinct numeric check-in "
            "days in the latest 28 local calendar days, and a latest check-in no more than seven "
            "days old. Histories of 5 days are withheld. The research sequence models are never "
            "loaded into the API. The endpoint only summarizes recent numeric valence and does "
            "not read journal/free-text data.",
            "",
            "## Reproducibility and limitations",
            "",
            f"- MLflow run: `{run_id}`; config fingerprint and environment are logged.",
            f"- Dataset manifest: {manifest['sha256']} (synthetic generated rows; no panel file or "
            "weights were written to Git).",
            "- Assumptions: weekly sine seasonality; individual slow trend and factor effects; "
            "user-specific AR(1) noise; logging missing-at-random given weekday/user rate.",
            "- Synthetic data cannot represent real variation, cultural context, measurement "
            "error, MNAR logging or clinical meaning. Valence is not a diagnosis or risk score.",
            "- No neural quality claim, clinical review, external validation or deployment "
            "approval is implied by a successful baseline run.",
            "",
        ]
    )
    return "\n".join(lines)


def run_mood_forecast_experiment(
    config: MoodForecastConfig,
    *,
    config_path: Path | None = None,
    allow_baselines_only: bool = False,
) -> dict[str, Any]:
    """Execute the synthetic benchmark and log provenance/metrics to MLflow."""
    has_torch = torch_available()
    if config.training.require_torch and not has_torch and not allow_baselines_only:
        raise TorchUnavailableError(
            "this evaluation config requires PyTorch; install ml[training] or rerun with "
            "--allow-baselines-only to produce an explicitly baseline-only report"
        )
    seed_state = seed_everything(config.seed, pin_blas_threads=config.threads)
    panel = generate_synthetic_panel(
        seed=config.seed,
        users=config.data.users,
        days=config.data.days,
        start_date=config.data.start_date,
        logging_probability=config.data.logging_probability,
    )
    manifest = panel_manifest(panel)
    windows = build_mood_windows(panel, config)
    data_ref = DatasetRef(
        name="synthetic_mood_panel",
        path=(
            f"synthetic://{panel.generator_version}/seed/{panel.seed}/sha256/{dataset_sha256(panel)}"
        ),
        sha256=dataset_sha256(panel),
        rows=panel.daily_rows,
    )

    with tracked_run(
        config,
        phase=config.run.phase,
        data_refs=(data_ref,),
        seed_state=seed_state,
        config_path=config_path,
        tags={
            "manovia.research_only": "true",
            "manovia.sequence_models": "available" if has_torch else "blocked-torch-absent",
        },
        params={
            "data.logged_days": manifest["logged_days"],
            "data.panel_sha256": manifest["sha256"],
            "features": FEATURE_NAMES,
            "torch.available": has_torch,
            "run.baselines_only_override": allow_baselines_only,
        },
    ) as run:
        scores, _baseline_predictions = _score_baselines(windows, config)
        best_baseline = min(scores, key=lambda name: scores[name]["mae"])
        summary: dict[str, Any] = {
            "config": config.name,
            "seed": config.seed,
            "torch_available": has_torch,
            "neural_status": "available" if has_torch else "blocked_torch_unavailable",
            "panel": manifest,
            "split": {
                "train_user_count": len(windows.split.train_users),
                "calibration_user_count": len(windows.split.calibration_users),
                "test_user_count": len(windows.split.test_users),
                "train_window_count": len(windows.train),
                "validation_window_count": len(windows.validation),
                "calibration_window_count": len(windows.calibration),
                "personalization_support_window_count": len(windows.personalization_support),
                "test_window_count": len(windows.test),
                "fit_cut": windows.split.fit_cut,
                "train_cut": windows.split.train_cut,
                "calibration_cut": windows.split.calibration_cut,
                "user_ids": {
                    "train": windows.split.train_users,
                    "calibration": windows.split.calibration_users,
                    "test": windows.split.test_users,
                },
            },
            "scores": scores,
            "best_baseline": best_baseline,
            "neural_scores": {},
            "ablation": {},
            "personalization": {},
            "neural_training": {},
        }
        if has_torch:
            for model_index, kind in enumerate(config.model.kinds):
                model, training = fit_sequence_model(
                    kind,
                    windows.train,
                    windows.validation,
                    n_features=len(FEATURE_NAMES),
                    hidden_size=config.model.hidden_size,
                    transformer_heads=config.model.transformer_heads,
                    transformer_layers=config.model.transformer_layers,
                    dropout=config.model.dropout,
                    epochs=config.training.epochs,
                    batch_size=config.training.batch_size,
                    learning_rate=config.training.learning_rate,
                    weight_decay=config.training.weight_decay,
                    gradient_clip=config.training.gradient_clip,
                    seed=config.seed + model_index,
                    threads=config.threads or 1,
                )
                calibration_raw = predict_quantiles(model, windows.calibration)
                calibrator = fit_interval_calibrator(
                    windows.calibration,
                    calibration_raw,
                    nominal_coverage=config.evaluation.nominal_coverage,
                )
                test_raw = predict_quantiles(model, windows.test)
                test_calibrated = apply_interval_calibrator(test_raw, calibrator)
                model_scores: dict[str, Any] = evaluate_predictions(
                    windows.test,
                    test_calibrated,
                    nominal_coverage=config.evaluation.nominal_coverage,
                )
                model_scores["calibration_corrections"] = list(calibrator.corrections)
                model_scores["calibration_counts"] = list(calibrator.calibration_counts)
                summary["neural_scores"][kind] = model_scores
                summary["neural_training"][kind] = training
                summary["personalization"][kind] = _few_shot_evaluation(
                    model, windows, test_raw, config
                )
                if config.evaluation.run_ablation:
                    summary["ablation"][kind] = {}
                    for group in config.evaluation.ablation_groups:
                        calibration_ablation = tuple(
                            replace(sample, features=ablate_features(sample.features, (group,)))
                            for sample in windows.calibration
                        )
                        test_ablation = tuple(
                            replace(sample, features=ablate_features(sample.features, (group,)))
                            for sample in windows.test
                        )
                        raw_calibration_ablation = predict_quantiles(model, calibration_ablation)
                        ablation_calibrator = fit_interval_calibrator(
                            calibration_ablation,
                            raw_calibration_ablation,
                            nominal_coverage=config.evaluation.nominal_coverage,
                        )
                        raw_test_ablation = predict_quantiles(model, test_ablation)
                        calibrated_ablation = apply_interval_calibrator(
                            raw_test_ablation, ablation_calibrator
                        )
                        summary["ablation"][kind][group] = evaluate_predictions(
                            test_ablation,
                            calibrated_ablation,
                            nominal_coverage=config.evaluation.nominal_coverage,
                        )
            summary["best_neural"] = min(
                summary["neural_scores"],
                key=lambda name: summary["neural_scores"][name]["mae"],
            )
            summary["neural_beats_best_baseline"] = (
                summary["neural_scores"][summary["best_neural"]]["mae"]
                < scores[best_baseline]["mae"]
            )
        else:
            summary["neural_beats_best_baseline"] = None
            summary["neural_block_reason"] = (
                "PyTorch is not installed; GRU/LSTM/Transformer training and feature ablation "
                "were not run. This is a real baseline-only evaluation, not a neural-model result."
            )

        safe_summary = _as_json(summary)
        run.log_dict(safe_summary, "results/summary.json")
        run.log_dict(manifest, "data/synthetic_manifest.json")
        for name, metrics in scores.items():
            run.log_metrics(
                {
                    f"baseline.{name}.{metric}": value
                    for metric, value in metrics.items()
                    if isinstance(value, (int, float))
                }
            )
        for name, metrics in summary["neural_scores"].items():
            run.log_metrics(
                {
                    f"sequence.{name}.{metric}": value
                    for metric, value in metrics.items()
                    if isinstance(value, (int, float))
                }
            )
        run.log_params(
            {
                "comparison.best_baseline": best_baseline,
                "comparison.best_neural": summary.get("best_neural", "not_measured"),
                "comparison.neural_beats_best_baseline": summary["neural_beats_best_baseline"],
                "splits.train_windows": len(windows.train),
                "splits.validation_windows": len(windows.validation),
                "splits.calibration_windows": len(windows.calibration),
                "splits.test_windows": len(windows.test),
            }
        )
        report = _render_report(
            config=config, summary=safe_summary, run_id=run.run_id, manifest=manifest
        )
        run.log_text(report, "reports/day36-mood-forecasting.md")
        report_path = resolve(config.report.output)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        summary["run_id"] = run.run_id
        summary["report_path"] = str(report_path)
        return cast(dict[str, Any], _as_json(summary))


__all__ = ["run_mood_forecast_experiment"]
