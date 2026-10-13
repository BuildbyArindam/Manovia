"""Day 27: the feature flags that gate risky features.

Two flags, two shapes:

* ``FEATURE_ML_THRESHOLDS_UNLOCKED`` — a **production fail-fast**: overriding
  the shipped safety-ML operating point in production refuses to boot unless
  the flag is set, so a threshold change can only be a deliberate, reviewed
  act. Development/tests stay unrestricted (tuning happens there).
* ``FEATURE_LLM_JUDGE`` — an **evals opt-in**: the advisory-only 4-axis judge
  refuses to run unless the environment flag is set, so a copied command line
  cannot enable it by accident.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core import crypto
from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
EVALS_DIR = REPO_ROOT / "evals"
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

import run_response_eval  # type: ignore[import-not-found]  # noqa: E402


def _production(**overrides: object) -> Settings:
    """Production settings that satisfy the other fail-fast checks."""
    base: dict[str, object] = {
        "app_env": "production",
        "secret_key": "p" * 64,
        "field_encryption_key": crypto.FernetCipher.generate_key(),
        "database_url": "postgresql+asyncpg://user:pass@db:5432/manovia",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


# --- the ML threshold unlock gate --------------------------------------------


def test_production_accepts_the_shipped_operating_point() -> None:
    settings = _production()
    assert settings.feature_ml_thresholds_unlocked is False
    assert settings.safety_ml_min_confidence == 0.70


def test_production_refuses_a_changed_threshold_without_the_flag() -> None:
    with pytest.raises(ValidationError, match="FEATURE_ML_THRESHOLDS_UNLOCKED"):
        _production(safety_ml_min_confidence=0.55)


def test_production_refuses_a_changed_mass_floor_without_the_flag() -> None:
    with pytest.raises(ValidationError, match="safety_ml_crisis_mass_floor"):
        _production(safety_ml_suspicion_floor=0.4, safety_ml_crisis_mass_floor=0.4)


def test_production_allows_a_changed_threshold_with_the_flag() -> None:
    settings = _production(safety_ml_min_confidence=0.55, feature_ml_thresholds_unlocked=True)
    assert settings.safety_ml_min_confidence == 0.55


def test_development_stays_unrestricted() -> None:
    """Tuning is development work; it must not require ceremony."""
    settings = Settings(_env_file=None, safety_ml_min_confidence=0.5)
    assert settings.safety_ml_min_confidence == 0.5


# --- the LLM judge flag --------------------------------------------------------


def test_the_judge_flag_defaults_to_off(settings: Settings) -> None:
    assert settings.feature_llm_judge is False


def test_the_judge_flag_parses_truthy_values(settings: Settings) -> None:
    for raw in ("1", "true", "YES"):
        assert Settings(_env_file=None, feature_llm_judge=raw).feature_llm_judge is True


def test_judge_gate_allows_deterministic_checks_without_a_flag() -> None:
    assert run_response_eval.judge_gate_error("none", env={}) is None


def test_judge_gate_blocks_the_judge_without_the_flag() -> None:
    error = run_response_eval.judge_gate_error("fake", env={})
    assert error is not None
    assert "FEATURE_LLM_JUDGE" in error


def test_judge_gate_allows_the_judge_with_the_flag() -> None:
    assert run_response_eval.judge_gate_error("fake", env={"FEATURE_LLM_JUDGE": "1"}) is None
    assert run_response_eval.judge_gate_error("llm", env={"FEATURE_LLM_JUDGE": "true"}) is None


def test_the_cli_refuses_with_judge_without_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real CLI exits 2 before running any scenario when unflagged."""
    monkeypatch.delenv("FEATURE_LLM_JUDGE", raising=False)
    exit_code = run_response_eval.main(["--provider", "fake", "--with-judge"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "FEATURE_LLM_JUDGE" in captured.err
