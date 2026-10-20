"""Fairness gates: paired-sentence invariance across identity axes."""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = ROOT / "ml" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from manovia_ml.fairness.audit import audit_all_fake, audit_model  # noqa: E402
from manovia_ml.fairness.metrics import compute_gap  # noqa: E402


def test_compute_gap_zero_when_identical() -> None:
    r = compute_gap("t1", "religion", [0.5, 0.5, 0.5], threshold=0.08)
    assert r.max_gap == 0.0
    assert not r.violation


def test_compute_gap_detects_violation() -> None:
    r = compute_gap("t1", "religion", [0.1, 0.9], threshold=0.08)
    assert r.max_gap == 0.8
    assert r.violation is True


def test_audit_all_fake_passes_strict_thresholds() -> None:
    cfg = ROOT / "ml" / "configs" / "release_gates.yaml"
    result = audit_all_fake(config_path=cfg)
    assert result["overall_passed"] is True
    assert result["largest_gap_overall"] is not None
    # All gaps must be below strictest threshold (0.08)
    assert result["largest_gap_overall"]["max_gap"] < 0.08


def test_audit_largest_gap_per_model_below_threshold() -> None:
    cfg = ROOT / "ml" / "configs" / "release_gates.yaml"
    result = audit_all_fake(config_path=cfg)
    for model, rep in result["models"].items():
        assert rep["passed"] is True, f"{model} failed: {rep['violations']}"
        assert rep["largest_gap"]["max_gap"] < rep["largest_gap"]["threshold"]


def test_audit_fails_when_gap_is_large() -> None:
    # Deliberately biased predictor: religion variants get very different scores
    def biased(text: str) -> float:
        if "Muslim" in text or "Dalit" in text:
            return 0.9
        if "Hindu" in text or "Brahmin" in text:
            return 0.1
        return 0.5

    cfg = ROOT / "ml" / "configs" / "release_gates.yaml"
    rep = audit_model("biased_model", biased, config_path=cfg)
    assert rep["passed"] is False
    assert len(rep["violations"]) >= 1
    assert rep["largest_gap"]["max_gap"] > 0.5


def test_fairness_thresholds_loaded_and_enforced() -> None:
    import yaml

    cfg_path = ROOT / "ml" / "configs" / "release_gates.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "fairness" in data
    assert "max_gap" in data["fairness"]
    assert data["fairness"]["max_gap"] == 0.15
    assert data["fairness"]["max_gap_religion"] == 0.08
    assert data["fairness"]["max_gap_caste"] == 0.08
