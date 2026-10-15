"""Day-36 deterministic simulator, leakage gates, baselines and reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
import yaml

from manovia_ml.mood_forecast.baselines import BASELINE_NAMES, baseline_quantiles, predict_baseline
from manovia_ml.mood_forecast.config import MoodForecastConfig, load_mood_forecast_config
from manovia_ml.mood_forecast.data import (
    FEATURE_GROUPS,
    FEATURE_INDEX,
    FEATURE_NAMES,
    ablate_features,
    assert_temporal_isolation,
    build_mood_windows,
)
from manovia_ml.mood_forecast.experiment import run_mood_forecast_experiment
from manovia_ml.mood_forecast.metrics import (
    apply_interval_calibrator,
    evaluate_predictions,
    fit_interval_calibrator,
)
from manovia_ml.mood_forecast.models import (
    TorchUnavailableError,
    build_sequence_model,
    fit_sequence_model,
    predict_quantiles,
    torch_available,
)
from manovia_ml.mood_forecast.personalization import personalize_last_layer
from manovia_ml.mood_forecast.synthetic import (
    MoodPanel,
    dataset_sha256,
    generate_synthetic_panel,
    iter_jsonl,
    panel_manifest,
)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "mood_forecast.yaml"


def _config(
    *,
    users: int = 12,
    days: int = 90,
    report_path: Path | None = None,
    tracking_uri: str | None = None,
) -> MoodForecastConfig:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["data"]["users"] = users
    raw["data"]["days"] = days
    raw["model"]["hidden_size"] = 8
    raw["model"]["transformer_heads"] = 1
    raw["model"]["kinds"] = ["gru", "lstm", "transformer"]
    raw["training"]["epochs"] = 1
    raw["training"]["batch_size"] = 8
    raw["training"]["few_shot_steps"] = 2
    raw["training"]["few_shot_min_support"] = 3
    raw["tracking"]["tracking_uri"] = tracking_uri
    if report_path is not None:
        raw["report"]["output"] = str(report_path)
    return MoodForecastConfig.model_validate(raw)


def _panel(config: MoodForecastConfig, *, seed: int | None = None) -> MoodPanel:
    return generate_synthetic_panel(
        seed=config.seed if seed is None else seed,
        users=config.data.users,
        days=config.data.days,
        start_date=config.data.start_date,
        logging_probability=config.data.logging_probability,
    )


def test_synthetic_panel_is_fixed_seed_deterministic_and_append_stable() -> None:
    first = generate_synthetic_panel(seed=33, users=5, days=90)
    again = generate_synthetic_panel(seed=33, users=5, days=90)
    larger = generate_synthetic_panel(seed=33, users=7, days=90)
    assert first == again
    assert first.users == larger.users[:5]
    assert dataset_sha256(first) == dataset_sha256(again)
    assert sum(1 for _ in iter_jsonl(first)) == first.daily_rows


def test_simulator_documents_individual_effects_missingness_and_scale() -> None:
    panel = generate_synthetic_panel(seed=9, users=12, days=90, logging_probability=0.75)
    assert panel.generator_version == "state-space-v1"
    assert panel.logged_days < panel.daily_rows
    assert len({user.parameters.baseline for user in panel.users}) > 1
    for user in panel.users:
        assert -0.007 <= user.parameters.trend_per_day <= 0.007
        assert 0.45 <= user.parameters.ar1 <= 0.78
        assert 0.5 <= user.parameters.logging_probability <= 1.0
        assert len(user.days) == 90
        assert any(not day.observed and day.valence is None for day in user.days)
        assert all(day.valence is None or 1 <= day.valence <= 5 for day in user.days)
        assert all(day.energy is None or 1 <= day.energy <= 5 for day in user.days)
    manifest = panel_manifest(panel)
    assumptions = cast(list[str], manifest["assumptions"])
    assert "AR(1)" in " ".join(assumptions)
    assert manifest["sha256"] == dataset_sha256(panel)


@pytest.mark.parametrize("seed", [-1, 2**32])
def test_simulator_rejects_non_four_byte_seeds(seed: int) -> None:
    with pytest.raises(ValueError, match="seed"):
        generate_synthetic_panel(seed=seed, users=5, days=90)


def test_config_rejects_unknown_keys_and_wrong_quantiles(tmp_path: Path) -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["unknown_model_knob"] = 4
    bad_unknown = tmp_path / "unknown.yaml"
    bad_unknown.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid mood-forecast config"):
        load_mood_forecast_config(bad_unknown)

    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["model"]["quantiles"] = [0.2, 0.5, 0.9]
    bad_quantiles = tmp_path / "quantiles.yaml"
    bad_quantiles.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="contract fixes quantiles"):
        load_mood_forecast_config(bad_quantiles)


def test_time_splits_are_user_disjoint_forward_only_and_fixed_shape() -> None:
    config = _config()
    windows = build_mood_windows(_panel(config), config)
    assert_temporal_isolation(windows)
    groups = [
        set(windows.split.train_users),
        set(windows.split.calibration_users),
        set(windows.split.test_users),
    ]
    assert not (groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2])
    assert all(sample.features.shape == (28, len(FEATURE_NAMES)) for sample in windows.train)
    assert all(sample.targets.shape == (3,) for sample in windows.test)
    assert windows.split.fit_cut < windows.split.train_cut < windows.split.calibration_cut
    assert all(sample.origin_index + 3 < windows.split.fit_cut for sample in windows.train)
    assert all(sample.origin_index + 1 >= windows.split.calibration_cut for sample in windows.test)


def test_all_four_baselines_forecast_three_days_on_the_scale() -> None:
    config = _config()
    windows = build_mood_windows(_panel(config), config)
    sample = windows.test[0]
    for baseline in BASELINE_NAMES:
        prediction = predict_baseline(sample, baseline, smoothing_alpha=0.35)
        assert prediction.shape == (3,)
        assert np.all((prediction >= 1.0) & (prediction <= 5.0))
        quantiles = baseline_quantiles(prediction)
        assert quantiles.shape == (3, 3)
        assert np.all(quantiles[:, 0] == quantiles[:, 1])
        assert np.all(quantiles[:, 1] == quantiles[:, 2])


def test_conformal_calibration_and_reported_metrics_mask_unlogged_targets() -> None:
    config = _config()
    windows = build_mood_windows(_panel(config), config)
    calibration = np.stack(
        [baseline_quantiles(predict_baseline(row, "persistence")) for row in windows.calibration]
    )
    calibrator = fit_interval_calibrator(windows.calibration, calibration, nominal_coverage=0.8)
    raw = np.stack(
        [baseline_quantiles(predict_baseline(row, "persistence")) for row in windows.test]
    )
    calibrated = apply_interval_calibrator(raw, calibrator)
    metrics = evaluate_predictions(windows.test, calibrated, nominal_coverage=0.8)
    assert all(value >= 0 for value in calibrator.corrections)
    assert 0 <= metrics["interval_coverage"] <= 1
    assert metrics["interval_width"] >= 0
    assert metrics["observed_targets"] == sum(
        int(sample.target_mask.sum()) for sample in windows.test
    )
    assert np.all(calibrated[:, :, 0] <= calibrated[:, :, 1])
    assert np.all(calibrated[:, :, 1] <= calibrated[:, :, 2])


def test_feature_ablation_is_neutral_and_does_not_mutate_input() -> None:
    config = _config()
    windows = build_mood_windows(_panel(config), config)
    original = windows.train[0].features.copy()
    ablated = ablate_features(original, ("context", "energy"))
    assert np.array_equal(original, windows.train[0].features)
    for group in ("context", "energy"):
        for feature in FEATURE_GROUPS[group]:
            assert np.all(ablated[..., FEATURE_INDEX[feature]] == 0.0)
    assert np.array_equal(
        ablate_features(original, ("context",))[:, FEATURE_INDEX["valence"]],
        original[:, FEATURE_INDEX["valence"]],
    )
    with pytest.raises(ValueError, match="unknown feature"):
        ablate_features(original, ("journal_text",))


def test_learned_models_fail_with_an_explicit_optional_dependency_message() -> None:
    if torch_available():
        pytest.skip("PyTorch is installed; architecture smoke test below exercises it")
    with pytest.raises(TorchUnavailableError, match="optional PyTorch training extra"):
        build_sequence_model(
            "gru",
            n_features=len(FEATURE_NAMES),
            hidden_size=8,
            transformer_heads=1,
            transformer_layers=1,
            dropout=0.1,
        )


def test_baseline_only_run_requires_an_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import manovia_ml.mood_forecast.experiment as experiment

    monkeypatch.setattr(experiment, "torch_available", lambda: False)
    with pytest.raises(TorchUnavailableError, match="--allow-baselines-only"):
        run_mood_forecast_experiment(_config())


def test_baseline_only_run_is_logged_and_never_claims_a_neural_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import manovia_ml.mood_forecast.experiment as experiment

    monkeypatch.setattr(experiment, "torch_available", lambda: False)
    config = _config(
        report_path=tmp_path / "day36.md",
        tracking_uri=f"file:{tmp_path / 'mlruns'}",
    )
    summary = run_mood_forecast_experiment(config, allow_baselines_only=True)
    report = Path(summary["report_path"]).read_text(encoding="utf-8")
    assert summary["neural_status"] == "blocked_torch_unavailable"
    assert summary["neural_beats_best_baseline"] is None
    assert set(summary["scores"]) == set(BASELINE_NAMES)
    assert "Neural result: not measured" in report
    assert "does not beat" not in report
    boundaries = summary["split"]
    assert (
        f"fit targets [0, {boundaries['fit_cut']}); validation targets "
        f"[{boundaries['fit_cut']}, {boundaries['train_cut']}); calibration targets "
        f"[{boundaries['train_cut']}, {boundaries['calibration_cut']})" in report
    )
    assert "Fit/validation/train/calibration cut indices" not in report
    assert summary["run_id"]
    from mlflow.tracking import MlflowClient

    tracking_uri = config.tracking.tracking_uri
    assert tracking_uri is not None
    client = MlflowClient(tracking_uri=tracking_uri)
    tracked = client.get_run(str(summary["run_id"]))
    assert tracked.data.tags["manovia.sequence_models"] == "blocked-torch-absent"
    assert tracked.data.params["comparison.best_neural"] == "not_measured"
    assert "baseline.moving_average_7d.mae" in tracked.data.metrics
    artifact_paths = {
        item.path for item in client.list_artifacts(str(summary["run_id"]), "results")
    }
    assert "results/summary.json" in artifact_paths
    assert (
        json.loads(json.dumps(summary))["panel"]["sha256"]
        == panel_manifest(_panel(config))["sha256"]
    )


@pytest.mark.slow
def test_gru_lstm_transformer_fit_with_ordered_quantiles_when_torch_is_installed() -> None:
    if not torch_available():
        pytest.skip("PyTorch is intentionally not installed in this sandbox")
    config = _config(users=12, days=110)
    windows = build_mood_windows(_panel(config), config)
    for kind in ("gru", "lstm", "transformer"):
        model, history = fit_sequence_model(
            kind,
            windows.train[:8],
            windows.validation[:4],
            n_features=len(FEATURE_NAMES),
            hidden_size=8,
            transformer_heads=1,
            transformer_layers=1,
            dropout=0.0,
            epochs=1,
            batch_size=4,
            learning_rate=0.001,
            weight_decay=0.0001,
            gradient_clip=1.0,
            seed=17,
        )
        predictions = predict_quantiles(model, windows.test[:3])
        assert predictions.shape == (3, 3, 3)
        assert np.isfinite(predictions).all()
        assert np.all(predictions[:, :, 0] <= predictions[:, :, 1])
        assert np.all(predictions[:, :, 1] <= predictions[:, :, 2])
        assert history["best_epoch"] == 1


def test_few_shot_below_minimum_is_withheld_without_importing_torch() -> None:
    config = _config()
    windows = build_mood_windows(_panel(config), config)
    result = personalize_last_layer(
        model=object(),
        support_samples=windows.personalization_support[:2],
        guard_samples=windows.test[:2],
        minimum_support=3,
    )
    assert result.accepted is False
    assert result.reason == "insufficient_support"
    assert result.update_norm == 0.0


@pytest.mark.slow
def test_few_shot_personalization_is_bounded_and_guard_gated() -> None:
    if not torch_available():
        pytest.skip("PyTorch is intentionally not installed in this sandbox")
    torch = pytest.importorskip("torch")

    config = _config(users=12, days=110)
    panel = generate_synthetic_panel(
        seed=config.seed,
        users=12,
        days=110,
        logging_probability=1.0,
    )
    windows = build_mood_windows(panel, config)
    support_by_user: dict[str, list[Any]] = {}
    for sample in windows.personalization_support:
        support_by_user.setdefault(sample.user_id, []).append(sample)
    candidate_support = next(rows for rows in support_by_user.values() if len(rows) >= 8)
    ordered = sorted(candidate_support, key=lambda sample: sample.origin_index)
    support = ordered[:4]
    guard = [
        sample for sample in ordered[4:] if sample.origin_index + 1 > support[-1].origin_index + 3
    ]
    assert guard

    class TinyHead(torch.nn.Module):  # type: ignore[misc, name-defined]
        def __init__(self) -> None:
            super().__init__()
            self.encoder = torch.nn.Linear(len(FEATURE_NAMES), 8)
            self.head = torch.nn.Linear(8, 9)

        def forward(self, values: Any) -> Any:
            encoded = self.encoder(values[:, -1, :])
            raw = self.head(encoded).reshape(-1, 3, 3).sort(dim=-1).values
            return 1 + 4 * torch.sigmoid(raw)

    model = TinyHead()
    untouched_encoder = model.encoder.weight.detach().clone()
    result = personalize_last_layer(
        model,
        support,
        guard,
        minimum_support=3,
        steps=3,
        learning_rate=0.005,
        l2=0.1,
        max_update_norm=0.25,
        max_validation_loss_increase=0.1,
    )
    assert result.validation_pinball_before is not None
    assert result.validation_pinball_after is not None
    assert result.validation_pinball_after <= result.validation_pinball_before + 0.1
    assert result.update_norm <= 0.25 + 1e-6
    assert torch.equal(model.encoder.weight, untouched_encoder)


def test_model_config_mentions_all_input_groups_and_horizon() -> None:
    config = load_mood_forecast_config(CONFIG_PATH)
    assert config.model.lookback_days == 28
    assert config.model.horizon_days == 3
    assert config.model.kinds == ("gru", "lstm", "transformer")
    assert config.evaluation.ablation_groups == ("context", "day_of_week", "energy")
