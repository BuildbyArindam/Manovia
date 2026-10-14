"""The ML safety classifier: calibrated per-level probabilities for one message.

Day 9 adds a statistical second opinion next to the rules engine. The interface
is the point of this module: the ensemble (:mod:`app.services.safety.ensemble`)
and the API only ever see :class:`MLPrediction` objects, so the implementation
can move from today's TF-IDF + logistic-regression baseline to a transformer
without touching a single caller.

Design constraints, in priority order:

1. **Offline, always.** No network at load or predict time. The model is a
   committed artifact (``app/ml_artifacts/crisis_v1/``) trained by
   ``evals/train_safety_classifier.py`` from the *train* split of the eval
   dataset only. A missing artifact (or a deployment without scikit-learn)
   yields :class:`NullClassifier`, and the pipeline degrades to rules-only.
2. **Conservative by construction.** Probabilities are calibrated
   (``CalibratedClassifierCV``), ties break toward the *higher* level, and the
   ensemble is the only consumer — where it is structurally unable to lower a
   rules result (see ``ensemble.combine``).
3. **Privacy like the rest of the package.** The classifier reads text and
   returns numbers. It stores nothing about the message, writes nothing, and
   logs nothing — this module contains no logging call, like every module here.

The baseline deliberately favours recall on HIGH/IMMINENT over precision: the
training objective uses balanced class weights, and the ensemble only acts on
the prediction when it is confident (thresholds live in ``app.core.config``).
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Final, Protocol

from app.services.safety.base import RiskLevel
from app.services.safety.normalise import DEFAULT_MAX_INPUT_CHARS

#: Wire labels, in ascending level order. The JSONL dataset stores the
#: upper-case forms; the classifier works in these lower-case ones.
LEVEL_LABELS: Final[tuple[str, ...]] = ("none", "low", "medium", "high", "imminent")

#: File names inside the artifact directory. Kept in one place because the
#: training script (``evals/train_safety_classifier.py``) writes exactly these.
MODEL_FILENAME: Final = "model.joblib"
METADATA_FILENAME: Final = "metadata.json"

#: The classifier reads the same window the rules engine reads: a megabyte
#: paste must not reach either matcher.
MAX_CLASSIFIER_CHARS: Final = DEFAULT_MAX_INPUT_CHARS

try:  # scikit-learn is a base dependency, but the safety package must import
    # and degrade gracefully even in a minimal environment.
    import joblib
except ImportError:  # pragma: no cover - exercised only without scikit-learn
    joblib = None


def preprocess_text(text: str) -> str:
    """The classifier's one normalisation step.

    Unicode NFC, casefold, and the shared character window. The rules engine
    does far more (leet, spelling, collapse); here the char n-gram features
    absorb typos themselves, and heavy normalisation would only make the two
    detectors clones of each other — the ensemble wants a second opinion, not
    an echo.
    """
    return unicodedata.normalize("NFC", text)[:MAX_CLASSIFIER_CHARS].casefold()


def preprocess_batch(texts: Sequence[str]) -> list[str]:
    """Vectorised form of :func:`preprocess_text` for pipeline steps."""
    return [preprocess_text(item) for item in texts]


@dataclass(frozen=True)
class MLPrediction:
    """Calibrated probabilities per level for one message, plus the argmax.

    ``probabilities`` always carries all five labels; they sum to 1 within
    floating-point tolerance. ``level`` is the argmax with ties broken toward
    the *higher* level — a safety classifier has no business resolving doubt
    downwards.
    """

    #: Label -> calibrated probability. Keys are ``LEVEL_LABELS``.
    probabilities: Mapping[str, float] = field(default_factory=dict)
    #: Argmax level (tie-broken upward).
    level: RiskLevel = RiskLevel.NONE
    #: The top-class probability — what the ensemble calls "confidence".
    confidence: float = 0.0
    #: Artifact version string, for audit trails and reports.
    version: str = "unknown"
    #: Day 33 (trained model only): the level after the conformal abstention
    #: policy — a prediction set containing MEDIUM or above is treated as MEDIUM
    #: or above. ``None`` for a classifier with no prediction sets, so a caller
    #: can tell "no policy" from "policy says NONE".
    abstained_level: RiskLevel | None = None
    #: Day 33: the conformal prediction set, most severe first. Empty when the
    #: classifier has no conformal quantile. A set with more than one element is
    #: an abstention: the model declined to name one level.
    prediction_set: tuple[str, ...] = ()

    def mass_at_or_above(self, level: RiskLevel) -> float:
        """Total probability on ``level`` and everything more severe."""
        return float(sum(self.probabilities[item.label] for item in RiskLevel if item >= level))


class SafetyClassifier(Protocol):
    """The interface every implementation hides behind."""

    #: False for :class:`NullClassifier`; callers then run rules-only.
    enabled: bool
    #: Artifact version, surfaced in responses and eval reports.
    version: str

    def predict(self, text: str) -> MLPrediction | None:
        """Score one message. ``None`` means "I cannot answer"."""
        ...


class NullClassifier:
    """The classifier that answers nothing: missing artifact, missing library,
    or the ensemble switched off. The pipeline then runs rules-only, which is
    exactly the Day 8 behaviour — degradation must never be a crash."""

    enabled: bool = False
    version: str = "disabled"

    def __init__(self, reason: str = "disabled") -> None:
        #: Why there is no model. Metadata only — safe to surface in a log at
        #: the API layer (the reason vocabulary is fixed here, never the text).
        self.reason = reason

    def predict(self, text: str) -> MLPrediction | None:
        return None


class TfidfLogisticClassifier:
    """The Day 9 baseline: char/word TF-IDF + logistic regression, calibrated.

    Wraps a fitted scikit-learn pipeline loaded from the committed artifact.
    Stateless and thread-safe after construction: ``predict_proba`` calls hold
    no per-request state.
    """

    enabled: bool = True

    def __init__(self, pipeline: object, classes: Sequence[str], version: str) -> None:
        # A fitted scikit-learn Pipeline. Typed Any: sklearn has no stubs and
        # this module must import without it (degradation); the artifact is
        # validated by shape checks instead of types.
        self._pipeline: Any = pipeline
        #: Training-time class order (sklearn's). ``predict_proba`` columns
        #: follow this order, so the vocabulary must match — the order may not
        #: (sklearn sorts it its own way and the artifact records that).
        if set(classes) != set(LEVEL_LABELS):
            raise ValueError(f"artifact classes {tuple(classes)!r} do not cover {LEVEL_LABELS!r}")
        self._classes = tuple(classes)
        self.version = version

    def predict(self, text: str) -> MLPrediction | None:
        """Return calibrated per-level probabilities for one message."""
        if not text or not text.strip():
            return None
        # The pipeline carries its own preprocessing step (preprocess_batch),
        # so the raw text goes in and training/inference cannot drift apart.
        probabilities = self._pipeline.predict_proba([text])[0]
        mapping = {
            label: float(value) for label, value in zip(self._classes, probabilities, strict=True)
        }

        # Tie-break toward the higher level: walk levels descending and keep
        # the first whose probability is within a hair of the maximum.
        best = max(mapping.values())
        level = RiskLevel.NONE
        # ``best`` is the maximum of this very mapping, so at least one
        # candidate always satisfies the comparison: the loop cannot fall
        # through. Declared rather than covered because no test can enter a
        # state where it does (Day 26).
        for candidate in reversed(tuple(RiskLevel)):  # pragma: no branch
            if mapping[candidate.label] >= best - 1e-12:
                level = candidate
                break

        return MLPrediction(
            probabilities=mapping,
            level=level,
            confidence=mapping[level.label],
            version=self.version,
        )


@lru_cache(maxsize=4)
def build_ml_classifier(*, artifact_dir: str = "") -> SafetyClassifier:
    """Load the committed artifact, or degrade to :class:`NullClassifier`.

    Keyword-only parameter on purpose: the package's privacy test asserts no
    cache in this package can be keyed on a message, and a message would have
    to be positional to be useful as a key.
    """
    if joblib is None:
        return NullClassifier(reason="scikit_learn_missing")

    base = (
        Path(artifact_dir)
        if artifact_dir.strip()
        else Path(str(files("app").joinpath("ml_artifacts/crisis_v1")))
    )
    model_path = base / MODEL_FILENAME
    metadata_path = base / METADATA_FILENAME
    if not model_path.is_file():
        return NullClassifier(reason="artifact_missing")

    bundle = joblib.load(model_path)
    pipeline = bundle.get("pipeline")
    classes = tuple(bundle.get("classes", ()))
    if pipeline is None or not classes:
        return NullClassifier(reason="artifact_invalid")

    version = "v1"
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        version = str(metadata.get("version", "v1"))

    try:
        return TfidfLogisticClassifier(pipeline, classes, version)
    except ValueError:
        return NullClassifier(reason="artifact_invalid")


def build_safety_classifier(settings: Any) -> SafetyClassifier:
    """The one place that decides *which* ML classifier the ensemble consumes.

    ``SAFETY_ML`` (``settings.safety_ml``) selects between the Day-9 baseline and
    the Day-33 trained model. Both construction sites — the API dependency and
    the chat orchestrator — call this, so a deployment cannot end up with the
    chat path on one model and the assess endpoint on the other.

    The order of the checks is the behaviour:

    1. ``SAFETY_ML_ENABLED=false`` -> a disabled classifier, and the pipeline is
       exactly the Day-8 rules-only one.
    2. ``SAFETY_ML=trained`` -> the packaged LoRA model. If it cannot be loaded
       (missing directory, missing torch/transformers, broken artefact) this
       falls back to the **baseline**, because "the ML is silently off" is a
       different deployment from the one the operator asked for, and the
       fallback is recorded in the classifier's ``version``/``reason``.
    3. ``SAFETY_ML=baseline`` (the default) -> the Day-9 model.

    Everything here is offline: no network at load or predict time.
    """

    if not getattr(settings, "safety_ml_enabled", False):
        return NullClassifier(reason="disabled")

    choice = str(getattr(settings, "safety_ml", "baseline") or "baseline").strip().lower()
    if choice == "trained":
        from app.services.safety.trained import (
            TrainedSafetyClassifier,
            build_trained_safety_classifier,
        )

        artifact_dir = str(getattr(settings, "safety_ml_trained_artifact_dir", "") or "")
        if not artifact_dir.strip():
            artifact_dir = str(
                Path(str(files("app").joinpath("ml_artifacts/safety_trained"))).resolve()
            )
        trained = build_trained_safety_classifier(artifact_dir=artifact_dir)
        # Probe the two directories here rather than trusting a lazy object: the
        # fallback below must be decided at construction time, not on the first
        # request that happens to need a prediction.
        if trained.enabled and isinstance(trained, TrainedSafetyClassifier):
            if trained.artifact_exists():
                return trained
        elif trained.enabled:
            return trained
        # Recorded, not silent: the fallback keeps the same fitted pipeline but
        # carries a version string that says the trained model was asked for and
        # could not be used, so a response or an eval report shows why. Without
        # this the deployment would look exactly like `SAFETY_ML=baseline`.
        baseline = build_ml_classifier(artifact_dir=str(settings.safety_ml_artifact_dir))
        if isinstance(baseline, TfidfLogisticClassifier):
            return TfidfLogisticClassifier(
                baseline._pipeline,
                baseline._classes,
                f"{baseline.version}+trained-unavailable",
            )
        return baseline

    if choice == "onnx":
        # Day 34: the distilled student from the registry (@production,
        # checksum-verified). Any failure — no registry configured, bad
        # checksum, missing onnxruntime — falls back to the baseline with
        # the reason in the version string, exactly like `trained` above.
        registry_dir = str(getattr(settings, "ml_runtime_registry_dir", "") or "")
        baseline = build_ml_classifier(artifact_dir=str(settings.safety_ml_artifact_dir))

        def _onnx_unavailable() -> SafetyClassifier:
            if isinstance(baseline, TfidfLogisticClassifier):
                return TfidfLogisticClassifier(
                    baseline._pipeline,
                    baseline._classes,
                    f"{baseline.version}+onnx-unavailable",
                )
            return baseline

        if not registry_dir.strip():
            return _onnx_unavailable()
        try:
            from app.services.ml_runtime.classifier import build_onnx_safety_classifier

            return build_onnx_safety_classifier(
                registry_dir=registry_dir,
                model_name=str(getattr(settings, "ml_runtime_model_name", "safety-student")),
                timeout_seconds=float(getattr(settings, "ml_runtime_timeout_seconds", 2.0)),
                max_batch_size=int(getattr(settings, "ml_runtime_max_batch_size", 16)),
                batch_wait_ms=float(getattr(settings, "ml_runtime_batch_wait_ms", 5.0)),
            )
        except Exception:  # checksum, registry, session: all degrade alike
            return _onnx_unavailable()

    return build_ml_classifier(artifact_dir=str(settings.safety_ml_artifact_dir))


__all__ = [
    "LEVEL_LABELS",
    "MAX_CLASSIFIER_CHARS",
    "METADATA_FILENAME",
    "MODEL_FILENAME",
    "MLPrediction",
    "NullClassifier",
    "SafetyClassifier",
    "TfidfLogisticClassifier",
    "build_ml_classifier",
    "build_safety_classifier",
    "preprocess_batch",
    "preprocess_text",
]
