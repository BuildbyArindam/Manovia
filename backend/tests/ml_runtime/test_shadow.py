"""Shadow mode: categories only, never text, never a changed response."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.services.ml_runtime.shadow import ShadowRunner, categorise, summarise


@pytest.mark.parametrize(
    ("production", "candidate", "expected"),
    [
        ("none", "none", "agree"),
        ("none", "low", "candidate_higher"),
        ("medium", "low", "candidate_lower"),
        ("none", "high", "candidate_crisis_only"),
        ("imminent", "medium", "production_crisis_only"),
        ("high", "high", "agree"),
        ("low", "imminent", "candidate_crisis_only"),
    ],
)
def test_categorise(production: str, candidate: str, expected: str) -> None:
    assert categorise(production, candidate) == expected


@dataclass
class _Prediction:
    probabilities: tuple[float, ...]


class _FakeCandidate:
    """Candidate double: levels by keyword, no ONNX needed."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.calls: list[str] = []

    def predict(self, text: str) -> _Prediction:
        self.calls.append(text)
        if self._fail:
            raise RuntimeError("candidate exploded")
        if "end my life" in text:
            return _Prediction((0.0, 0.0, 0.0, 0.0, 1.0))
        return _Prediction((1.0, 0.0, 0.0, 0.0, 0.0))


_LABELS = ("none", "low", "medium", "high", "imminent")


def test_runner_records_categories_only(tmp_path: Path) -> None:
    log = tmp_path / "shadow.jsonl"
    runner = ShadowRunner(
        _FakeCandidate(),
        candidate_version="cand-v1",
        candidate_labels=_LABELS,
        log_path=log,
    )
    runner.observe(production_level="none", production_version="prod-v1", text="hello there")
    runner.observe(
        production_level="none", production_version="prod-v1", text="i want to end my life"
    )
    runner.flush()
    summary = summarise(runner.records)
    assert summary["total"] == 2
    assert summary["counts"]["agree"] == 1
    assert summary["counts"]["candidate_crisis_only"] == 1
    # The log line carries categories and versions — and no text field at all.
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert all("text" not in line for line in lines)
    assert {line["category"] for line in lines} == {"agree", "candidate_crisis_only"}


def test_runner_records_candidate_errors_as_categories() -> None:
    runner = ShadowRunner(
        _FakeCandidate(fail=True), candidate_version="cand-v1", candidate_labels=_LABELS
    )
    runner.observe(production_level="low", production_version="prod-v1", text="anything")
    runner.flush()
    assert runner.records[0].category in {"candidate_error", "candidate_timeout"}


def test_shadow_never_changes_the_response() -> None:
    """The pipeline contract: identical assessments with shadow on and off.

    Exercised at the ``run_ensemble`` seam with a rules engine and a stub
    classifier, so the assertion is about the pipeline, not the transport.
    """

    import asyncio

    from app.services.safety.base import RiskLevel
    from app.services.safety.ml_classifier import MLPrediction
    from app.services.safety.pipeline import run_ensemble
    from app.services.safety.rules import build_engine

    class _Classifier:
        enabled = True
        version = "stub-v1"

        def predict(self, text: str) -> MLPrediction:
            return MLPrediction(
                probabilities={
                    "none": 0.9,
                    "low": 0.1,
                    "medium": 0.0,
                    "high": 0.0,
                    "imminent": 0.0,
                },
                level=RiskLevel.NONE,
                confidence=0.9,
                version="stub-v1",
            )

    async def _run(shadow: Any) -> Any:
        return await run_ensemble(
            "i feel a bit tired today",
            "en",
            build_engine(),
            _Classifier(),
            min_confidence=0.7,
            crisis_mass_floor=0.3,
            suspicion_floor=0.25,
            shadow_runner=shadow,
        )

    runner = ShadowRunner(_FakeCandidate(), candidate_version="cand-v1", candidate_labels=_LABELS)
    without, decision_without = asyncio.run(_run(None))
    with_shadow, decision_with = asyncio.run(_run(runner))
    runner.flush()
    assert with_shadow.level == without.level
    assert decision_with.level == decision_without.level
    assert decision_with.source == decision_without.source
    assert runner.records, "shadow should have observed the message"
