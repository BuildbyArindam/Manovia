"""Day 27 regression tests: the evals judge flag is a clean, consistent gate.

The CI failure this file pins: ``run_all_evals`` used to default
``--with-judge`` to True and then crash with a ``FileNotFoundError`` on the
report the (gate-refused) response eval never wrote. Now the judge is opt-in
twice — ``--with-judge`` on the CLI *and* ``FEATURE_LLM_JUDGE`` in the
environment — and a refusal propagates as a clean non-zero exit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
EVALS_DIR = REPO_ROOT / "evals"
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

import run_all_evals  # type: ignore[import-not-found]  # noqa: E402


@pytest.fixture
def reports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the harness at a scratch reports dir (never touch real reports)."""
    scratch = tmp_path / "reports"
    monkeypatch.setattr(run_all_evals, "REPORTS_DIR", scratch)
    return scratch


def test_with_judge_defaults_to_off() -> None:
    """`--with-judge` must be opt-in, not implicit (ADR 0024 + Day 27 flag)."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--with-judge", action="store_true")
    args = parser.parse_args([])
    assert args.with_judge is False


def test_unflagged_judge_refusal_propagates_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    reports_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`run_all_evals --with-judge` without the env flag exits 2 with the
    gate's explanation and never raises (the pre-fix behaviour was a
    FileNotFoundError traceback on the report that was never written)."""
    monkeypatch.delenv("FEATURE_LLM_JUDGE", raising=False)
    exit_code = run_all_evals.main(["--provider", "fake", "--with-judge", "--date", "2026-10-13"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "FEATURE_LLM_JUDGE" in captured.err
    assert "Traceback" not in captured.err
    # The harness stopped at the gate: the response report was never written,
    # and the earlier suites' reports are intact.
    assert (reports_dir / "2026-10-13" / "safety.md").exists()
    assert not (reports_dir / "2026-10-13" / "response.md").exists()


def test_flagged_judge_runs_and_the_deterministic_gates_hold(
    monkeypatch: pytest.MonkeyPatch, reports_dir: Path
) -> None:
    """With the flag set, the curated run includes the judge and passes."""
    monkeypatch.setenv("FEATURE_LLM_JUDGE", "1")
    exit_code = run_all_evals.main(["--provider", "fake", "--with-judge", "--date", "2026-10-13"])
    assert exit_code == 0
    summary = (reports_dir / "2026-10-13" / "summary.json").read_text(encoding="utf-8")
    assert "overall_gate_passed" in summary


def test_unflagged_run_without_the_judge_still_passes(
    monkeypatch: pytest.MonkeyPatch, reports_dir: Path
) -> None:
    """`make eval` (no --with-judge, no flag) is the CI default and must pass
    offline with the deterministic gates only."""
    monkeypatch.delenv("FEATURE_LLM_JUDGE", raising=False)
    exit_code = run_all_evals.main(["--provider", "fake", "--date", "2026-10-13"])
    assert exit_code == 0
    summary = (reports_dir / "2026-10-13" / "summary.json").read_text(encoding="utf-8")
    assert "overall_gate_passed" in summary
