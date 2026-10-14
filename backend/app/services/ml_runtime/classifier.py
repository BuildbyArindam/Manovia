"""The ONNX student behind the existing ``SafetyClassifier`` interface.

The ensemble sees an :class:`MLPrediction` with the same five-label
distribution the Day-9 baseline produces, so ``ensemble.combine`` and its
``max(rules, ml)`` invariant are untouched: this classifier can only ever
raise a level, exactly like every classifier before it (ADR 0009/0034).

Fallback ladder, in order (each rung verified the same way):

1. registry ``@production`` (checksum-verified at load);
2. registry ``@previous_production`` (runtime-internal, on load/timeout
   failure);
3. the pre-Day-34 classifier the operator configured (``SAFETY_ML``) —
   decided at construction, so a broken registry degrades to a known
   model, never to rules-only silence.
"""

from __future__ import annotations

from pathlib import Path

from app.services.ml_runtime.registry import LoadedModel, RegistryLoader
from app.services.ml_runtime.runtime import ModelRuntimeUnavailableError, OnnxModelRuntime
from app.services.safety.base import RiskLevel
from app.services.safety.ml_classifier import LEVEL_LABELS, MLPrediction

__all__ = ["OnnxSafetyClassifier", "build_onnx_safety_classifier"]

_BY_LABEL = {level.label: level for level in RiskLevel}


class OnnxSafetyClassifier:
    """A registry-loaded ONNX student as a ``SafetyClassifier``."""

    enabled: bool = True

    def __init__(self, runtime: OnnxModelRuntime, *, labels: tuple[str, ...]) -> None:
        if set(labels) != set(LEVEL_LABELS):
            raise ValueError(f"ONNX labels {labels!r} do not cover {LEVEL_LABELS!r}")
        self._runtime = runtime
        self._labels = labels
        self.version = f"onnx-{runtime.version}"

    @property
    def runtime(self) -> OnnxModelRuntime:
        return self._runtime

    def predict(self, text: str) -> MLPrediction | None:
        if not text or not text.strip():
            return None
        try:
            prediction = self._runtime.predict(text)
        except ModelRuntimeUnavailableError:
            return None
        probabilities = {
            label: float(prediction.probabilities[index])
            for index, label in enumerate(self._labels)
        }
        # Tie-break toward the higher level, like the Day-9 baseline.
        best = max(probabilities.values())
        level = RiskLevel.NONE
        for candidate in reversed(tuple(RiskLevel)):
            if probabilities[candidate.label] >= best - 1e-12:
                level = candidate
                break
        return MLPrediction(
            probabilities=probabilities,
            level=level,
            confidence=probabilities[level.label],
            version=self.version,
        )


def build_onnx_safety_classifier(
    *,
    registry_dir: str | Path,
    model_name: str,
    timeout_seconds: float = 2.0,
    max_batch_size: int = 16,
    batch_wait_ms: float = 5.0,
) -> OnnxSafetyClassifier:
    """Load ``@production`` (verified) with the previous version as fallback.

    Raises nothing: on any failure the caller gets ``None``-predicting
    behaviour via the returned object's runtime failure record... in
    practice construction failures raise :class:`ModelRuntimeUnavailableError`
    and the factory in ``ml_classifier.build_safety_classifier`` decides
    the fallback. This builder stays strict so tests can see the failure.
    """

    from app.services.ml_runtime.runtime import build_runtime_from_registry

    loader = RegistryLoader(registry_dir)
    primary: LoadedModel = loader.load(model_name, alias="production")
    runtime = build_runtime_from_registry(
        registry_dir,
        model_name,
        timeout_seconds=timeout_seconds,
        max_batch_size=max_batch_size,
        batch_wait_ms=batch_wait_ms,
    )
    return OnnxSafetyClassifier(runtime, labels=primary.labels)
