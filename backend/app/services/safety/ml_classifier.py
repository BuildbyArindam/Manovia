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
    import joblib  # type: ignore[import-untyped]
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
        for candidate in reversed(tuple(RiskLevel)):
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
    "preprocess_batch",
    "preprocess_text",
]
