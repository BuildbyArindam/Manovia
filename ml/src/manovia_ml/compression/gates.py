"""Release gates: the numbers a compressed candidate must not cross.

``ml/configs/release_gates.yaml`` is the contract; this module loads it
(``extra=forbid``, like every config in this package) and evaluates a
candidate's measured metrics against it. A failed gate rejects the
candidate: no promotion, no serving. The check is a pure function of
(measured metrics, gates) so the promotion path, the CLI and the tests
all get the same answer from the same numbers.

Metric names the evaluator understands (all optional in the input except
where a gate needs them — a missing metric the gate needs is a failure,
never a silent pass):

* ``safety_crisis_recall`` / ``safety_accuracy`` — candidate on frozen eval
* ``teacher_safety_crisis_recall``, ``fp32_safety_crisis_recall``
* ``emotion_macro_f1``, ``teacher_emotion_macro_f1``, ``fp32_emotion_macro_f1``
* ``p50_latency_ms`` / ``p95_latency_ms`` — candidate serving latency
* ``student_size_mb`` / ``teacher_size_mb`` / ``int8_size_mb`` / ``fp32_size_mb``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from manovia_ml.config import StrictModel
from manovia_ml.paths import config_dir

__all__ = [
    "GateCheck",
    "GateReport",
    "ReleaseGates",
    "evaluate_gates",
    "load_release_gates",
]


class SafetyGates(StrictModel):
    min_crisis_recall: float = Field(ge=0.0, le=1.0)
    max_crisis_recall_drop_vs_teacher: float = Field(ge=0.0, le=1.0)
    max_crisis_recall_drop_int8_vs_fp32: float = Field(ge=0.0, le=1.0)
    min_accuracy: float = Field(ge=0.0, le=1.0)


class EmotionGates(StrictModel):
    min_macro_f1: float = Field(ge=0.0, le=1.0)
    max_macro_f1_drop_vs_teacher: float = Field(ge=0.0, le=1.0)
    max_macro_f1_drop_int8_vs_fp32: float = Field(ge=0.0, le=1.0)


class ServingGates(StrictModel):
    max_p95_latency_ms: float = Field(gt=0.0)
    max_p50_latency_ms: float = Field(gt=0.0)


class SizeGates(StrictModel):
    max_student_teacher_ratio: float = Field(gt=0.0)
    max_int8_fp32_ratio: float = Field(gt=0.0)


class FairnessGates(StrictModel):
    """Paired-sentence invariance gaps (Day 48). Extra=forbid, all optional with defaults."""

    max_gap: float = Field(default=0.15, ge=0.0, le=1.0)
    max_gap_religion: float = Field(default=0.08, ge=0.0, le=1.0)
    max_gap_caste: float = Field(default=0.08, ge=0.0, le=1.0)
    max_gap_gendered_language: float = Field(default=0.08, ge=0.0, le=1.0)
    max_gap_disability: float = Field(default=0.08, ge=0.0, le=1.0)
    max_gap_sexuality: float = Field(default=0.08, ge=0.0, le=1.0)
    max_gap_region: float = Field(default=0.10, ge=0.0, le=1.0)
    max_gap_language: float = Field(default=0.12, ge=0.0, le=1.0)
    max_gap_script: float = Field(default=0.12, ge=0.0, le=1.0)
    max_gap_code_mixing: float = Field(default=0.12, ge=0.0, le=1.0)
    max_gap_formality: float = Field(default=0.12, ge=0.0, le=1.0)
    max_gap_dialect_spelling: float = Field(default=0.12, ge=0.0, le=1.0)


class ReleaseGates(StrictModel):
    """The validated contents of ``release_gates.yaml``."""

    safety: SafetyGates
    emotion: EmotionGates
    serving: ServingGates
    size: SizeGates
    fairness: FairnessGates | None = None


def load_release_gates(path: str | Path | None = None) -> ReleaseGates:
    candidate = Path(path) if path is not None else config_dir() / "release_gates.yaml"
    if not candidate.is_file():
        raise FileNotFoundError(f"release gates not found: {candidate}")
    raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{candidate}: release gates must be a mapping")
    return ReleaseGates.model_validate(raw)


@dataclass(frozen=True)
class GateCheck:
    """One gate, its inputs and its verdict — the report's rows."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class GateReport:
    checks: tuple[GateCheck, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[GateCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [
                {"name": check.name, "passed": check.passed, "detail": check.detail}
                for check in self.checks
            ],
        }


def _drop_check(
    checks: list[GateCheck],
    name: str,
    candidate: float | None,
    reference: float | None,
    max_drop: float,
) -> None:
    if candidate is None or reference is None:
        checks.append(GateCheck(name, False, "missing metric: cannot prove the drop is bounded"))
        return
    drop = reference - candidate
    checks.append(
        GateCheck(
            name,
            drop <= max_drop + 1e-12,
            f"drop {drop:.4f} (candidate {candidate:.4f} vs reference {reference:.4f}, "
            f"allowed {max_drop:.4f})",
        )
    )


def evaluate_gates(metrics: dict[str, float], gates: ReleaseGates, *, task: str) -> GateReport:
    """Evaluate one candidate. ``task`` selects the task gates that apply.

    Latency and size gates always apply when their metrics are present; a
    candidate scored without latency numbers has not been measured for
    serving and fails those gates rather than skipping them.
    """

    if task not in {"safety", "emotion"}:
        raise ValueError(f"unknown task {task!r}")
    checks: list[GateCheck] = []

    if task == "safety":
        recall = metrics.get("safety_crisis_recall")
        checks.append(
            GateCheck(
                "safety.min_crisis_recall",
                recall is not None and recall >= gates.safety.min_crisis_recall,
                f"crisis recall {recall} vs floor {gates.safety.min_crisis_recall}",
            )
        )
        _drop_check(
            checks,
            "safety.max_crisis_recall_drop_vs_teacher",
            recall,
            metrics.get("teacher_safety_crisis_recall"),
            gates.safety.max_crisis_recall_drop_vs_teacher,
        )
        _drop_check(
            checks,
            "safety.max_crisis_recall_drop_int8_vs_fp32",
            recall,
            metrics.get("fp32_safety_crisis_recall"),
            gates.safety.max_crisis_recall_drop_int8_vs_fp32,
        )
        accuracy = metrics.get("safety_accuracy")
        checks.append(
            GateCheck(
                "safety.min_accuracy",
                accuracy is not None and accuracy >= gates.safety.min_accuracy,
                f"accuracy {accuracy} vs floor {gates.safety.min_accuracy}",
            )
        )
    else:
        f1 = metrics.get("emotion_macro_f1")
        checks.append(
            GateCheck(
                "emotion.min_macro_f1",
                f1 is not None and f1 >= gates.emotion.min_macro_f1,
                f"macro-F1 {f1} vs floor {gates.emotion.min_macro_f1}",
            )
        )
        _drop_check(
            checks,
            "emotion.max_macro_f1_drop_vs_teacher",
            f1,
            metrics.get("teacher_emotion_macro_f1"),
            gates.emotion.max_macro_f1_drop_vs_teacher,
        )
        _drop_check(
            checks,
            "emotion.max_macro_f1_drop_int8_vs_fp32",
            f1,
            metrics.get("fp32_emotion_macro_f1"),
            gates.emotion.max_macro_f1_drop_int8_vs_fp32,
        )

    p95 = metrics.get("p95_latency_ms")
    checks.append(
        GateCheck(
            "serving.max_p95_latency_ms",
            p95 is not None and p95 <= gates.serving.max_p95_latency_ms,
            f"p95 {p95} ms vs budget {gates.serving.max_p95_latency_ms} ms",
        )
    )
    p50 = metrics.get("p50_latency_ms")
    checks.append(
        GateCheck(
            "serving.max_p50_latency_ms",
            p50 is not None and p50 <= gates.serving.max_p50_latency_ms,
            f"p50 {p50} ms vs budget {gates.serving.max_p50_latency_ms} ms",
        )
    )

    student, teacher = metrics.get("student_size_mb"), metrics.get("teacher_size_mb")
    if student is None or teacher is None or teacher <= 0:
        checks.append(GateCheck("size.max_student_teacher_ratio", False, "missing size metric"))
    else:
        ratio = student / teacher
        checks.append(
            GateCheck(
                "size.max_student_teacher_ratio",
                ratio <= gates.size.max_student_teacher_ratio,
                f"student/teacher {ratio:.4f} vs max {gates.size.max_student_teacher_ratio}",
            )
        )
    int8, fp32 = metrics.get("int8_size_mb"), metrics.get("fp32_size_mb")
    if int8 is None or fp32 is None or fp32 <= 0:
        checks.append(GateCheck("size.max_int8_fp32_ratio", False, "missing size metric"))
    else:
        ratio = int8 / fp32
        checks.append(
            GateCheck(
                "size.max_int8_fp32_ratio",
                ratio <= gates.size.max_int8_fp32_ratio,
                f"int8/fp32 {ratio:.4f} vs max {gates.size.max_int8_fp32_ratio}",
            )
        )
    return GateReport(checks=tuple(checks))
