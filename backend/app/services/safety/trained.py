"""The fine-tuned safety classifier, behind the existing ML interface.

``SAFETY_ML=trained`` leads the safety ensemble with the LoRA model from Day 33
(`ml/src/manovia_ml/training/safety/`), packaged as a directory:

| file | what it is |
| --- | --- |
| ``model/`` | merged weights (no PEFT adapter to load) + config |
| ``tokenizer/`` | the tokenizer that was trained with it |
| ``calibration.json`` | the temperature fitted on the calibration split |
| ``conformal.json`` | the conformal quantile, coverage and abstention policy |
| ``thresholds.json`` | temperature, quantile, decision rule, abstention policy |
| ``manifest.json`` | base model, labels, data hashes, run id, library versions |
| ``MODEL_CARD.md`` | the card, including the limitations |

Design rules, all inherited from the Day-9 classifier and the ensemble:

* **Lazy and thread-safe.** First use loads, exactly once, under a lock. A failed
  load is remembered, so a missing artefact directory costs one failed attempt
  rather than one per request.
* **Never fatal.** A missing directory, a missing ``transformers``/``torch``
  install, or an exception mid-inference becomes :class:`ModelUnavailableError`
  and the factory hands back the Day-9 baseline (or a null classifier when the
  artifact is missing too). Deleting the weights is a supported state.
* **The schema is unchanged.** The result is an :class:`MLPrediction` with the
  same five-label ``probabilities`` distribution, so ``ensemble.combine`` and the
  API see exactly what they saw on Day 9. The extra fields this module fills in
  (``abstained_level``, ``prediction_set``) are additive: nothing that does not
  read them behaves differently.
* **Only ever raises.** This module never lowers anything. The abstention policy
  it applies is the same direction of travel as the ensemble's suspicion band:
  ambiguity resolves toward noticing, never toward dismissing (ADR 0009).

Nothing here computes; the model does. This module's job is to be the *only*
place that knows the artefact layout, so a layout change is one file.
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from app.services.nlp.hf import ModelUnavailableError
from app.services.safety.base import RiskLevel
from app.services.safety.ml_classifier import (
    LEVEL_LABELS,
    MLPrediction,
    SafetyClassifier,
)

__all__ = [
    "ARTIFACT_FILES",
    "DEFAULT_QUANTILE",
    "DEFAULT_TEMPERATURE",
    "TrainedSafetyClassifier",
    "build_trained_safety_classifier",
]

#: The layout, in one place. The ML package writes exactly these
#: (`manovia_ml.training.safety.model`); the test suite checks the two agree.
MODEL_DIRNAME: Final = "model"
TOKENIZER_DIRNAME: Final = "tokenizer"
CALIBRATION_FILENAME: Final = "calibration.json"
CONFORMAL_FILENAME: Final = "conformal.json"
THRESHOLDS_FILENAME: Final = "thresholds.json"
MANIFEST_FILENAME: Final = "manifest.json"
CARD_FILENAME: Final = "MODEL_CARD.md"

ARTIFACT_FILES: Final[tuple[str, ...]] = (
    MODEL_DIRNAME,
    TOKENIZER_DIRNAME,
    CALIBRATION_FILENAME,
    CONFORMAL_FILENAME,
    THRESHOLDS_FILENAME,
    MANIFEST_FILENAME,
    CARD_FILENAME,
)

#: Fallbacks when the artefact predates a field, or the file is unreadable.
#: Both are "do nothing": T = 1 is the unscaled model, and a quantile of 1.0
#: makes every set the full label space — maximum abstention, never a silent
#: narrowing of what the model was unsure about.
DEFAULT_TEMPERATURE: Final = 1.0
DEFAULT_QUANTILE: Final = 1.0

#: The abstention floor: a set containing this level or anything more severe is
#: treated as at least this level (docs/safety-design.md §12.7).
ABSTENTION_FLOOR: Final = "medium"

_SEVERITY: Final[dict[str, int]] = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "imminent": 4,
}


def _severity(label: str) -> int:
    return _SEVERITY.get(label.strip().lower(), 0)


def _prediction_set(probabilities: Mapping[str, float], quantile: float) -> frozenset[str]:
    """Every level whose nonconformity score (1 - p) is at or below the quantile.

    A mirror of ``manovia_ml.training.safety.conformal.prediction_set``: the
    backend does not import from ``ml/``, so the rule is restated here and the
    test suite pins the two against each other.
    """

    members = {
        label
        for label in LEVEL_LABELS
        if 1.0 - float(probabilities.get(label, 0.0)) <= quantile + 1e-12
    }
    if not members:
        best = max(LEVEL_LABELS, key=lambda label: float(probabilities.get(label, 0.0)))
        members = {best}
    return frozenset(members)


def _abstain_level(prediction: frozenset[str], *, point: str) -> str:
    """The level the system acts on: a risky set means MEDIUM or above.

    The MEDIUM floor, not the most severe level in the set: the set lists what
    the model could not rule out, and promoting to the worst of those would make
    every uncertain row a crisis card. How far above MEDIUM a message goes is the
    point prediction's job, and the ensemble's crisis-mass band's.
    """

    risky = any(_severity(label) >= _severity(ABSTENTION_FLOOR) for label in prediction)
    if not risky:
        return point
    return max((point, ABSTENTION_FLOOR), key=_severity)


_BY_LABEL: Final[dict[str, RiskLevel]] = {level.label: level for level in RiskLevel}


def _as_level(label: str) -> RiskLevel:
    return _BY_LABEL.get(label.strip().lower(), RiskLevel.NONE)


class TrainedSafetyClassifier:
    """A packaged cost-sensitive safety model, loaded on first use, never fatal."""

    enabled: bool = True

    def __init__(
        self,
        directory: str | Path,
        *,
        max_length: int = 96,
        batch_size: int = 8,
        device: str = "cpu",
        abstain: bool = True,
        loader: Callable[[Path, str, int], tuple[Any, Any]] | None = None,
    ) -> None:
        path = Path(directory).expanduser()
        self._directory = path
        if max_length < 8:
            raise ValueError("max_length must be at least 8 tokens")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._max_length = max_length
        self._batch_size = batch_size
        self._device = device
        self._abstain = abstain
        self._loader = loader
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._labels: tuple[str, ...] = LEVEL_LABELS
        self._temperature = DEFAULT_TEMPERATURE
        self._quantile = DEFAULT_QUANTILE
        self._decision_rule = "expected_cost"
        self._manifest: dict[str, Any] = {}
        self._load_error: ModelUnavailableError | None = None
        self._lock = threading.Lock()
        self._load_count = 0
        self._failure: str = "none"
        # A plain attribute, not a property: SafetyClassifier declares `version`
        # as a settable variable (the Day-9 classifier assigns it in __init__),
        # and the protocol check is what keeps the two implementations
        # interchangeable. It is provisional until the manifest is read.
        self.version = "day33-pending"

    # --- introspection -------------------------------------------------------

    @property
    def directory(self) -> Path:
        return self._directory

    def _compute_version(self) -> str:
        """Provenance in one short string: the run id when there is one."""

        run_id = self._manifest.get("run_id")
        if isinstance(run_id, str) and run_id:
            return f"day33-{run_id[:8]}"
        base = self._manifest.get("base_model")
        if isinstance(base, str) and base:
            return f"day33-{base}"
        return "day33-untagged"

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def quantile(self) -> float:
        return self._quantile

    @property
    def labels(self) -> tuple[str, ...]:
        return self._labels

    @property
    def max_length(self) -> int:
        return self._max_length

    @property
    def load_count(self) -> int:
        """How many times the model was actually loaded (tests: must stay 1)."""

        return self._load_count

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def describe(self) -> dict[str, object]:
        """Provenance for logs and the dev endpoint: never raw user text."""

        return {
            "directory": str(self._directory),
            "loaded": self.is_loaded,
            "load_count": self._load_count,
            "device": self._device,
            "max_length": self._max_length,
            "labels": list(self._labels),
            "temperature": round(self._temperature, 4),
            "conformal_quantile": round(self._quantile, 4),
            "decision_rule": self._decision_rule,
            "abstention_enabled": self._abstain,
            "failure": self._failure,
            "base_model": self._manifest.get("base_model"),
            "run_id": self._manifest.get("run_id"),
            "trained_at": self._manifest.get("written"),
        }

    # --- loading -------------------------------------------------------------

    def artifact_exists(self) -> bool:
        """Whether the two directories a load needs are both present."""

        return (self._directory / MODEL_DIRNAME).is_dir() and (
            self._directory / TOKENIZER_DIRNAME
        ).is_dir()

    def _read_metadata(self) -> None:
        manifest_path = self._directory / MANIFEST_FILENAME
        if manifest_path.is_file():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                self._manifest = payload
                labels = payload.get("labels")
                if isinstance(labels, list) and labels:
                    candidate = tuple(str(label) for label in labels)
                    if set(candidate) == set(LEVEL_LABELS):
                        self._labels = candidate

        calibration_path = self._directory / CALIBRATION_FILENAME
        if calibration_path.is_file():
            try:
                payload = json.loads(calibration_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            temperature = self._number(payload, "temperature")
            if temperature is not None and temperature > 0 and math.isfinite(temperature):
                self._temperature = temperature

        conformal_path = self._directory / CONFORMAL_FILENAME
        if conformal_path.is_file():
            try:
                payload = json.loads(conformal_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            quantile = self._number(payload, "quantile")
            if quantile is not None and 0.0 <= quantile <= 1.0:
                self._quantile = quantile

        thresholds_path = self._directory / THRESHOLDS_FILENAME
        if thresholds_path.is_file():
            try:
                payload = json.loads(thresholds_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                rule = payload.get("decision_rule")
                if isinstance(rule, str) and rule:
                    self._decision_rule = rule
                temperature = self._number(payload, "temperature")
                if temperature is not None and temperature > 0 and math.isfinite(temperature):
                    self._temperature = temperature
                quantile = self._number(payload, "conformal_quantile")
                if quantile is not None and 0.0 <= quantile <= 1.0:
                    self._quantile = quantile

    @staticmethod
    def _number(payload: Any, key: str) -> float | None:
        if not isinstance(payload, Mapping):
            return None
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value)

    def _default_loader(self, directory: Path, device: str, max_length: int) -> tuple[Any, Any]:
        """Import transformers/torch here: the offline suite must import this module."""

        import torch  # noqa: F401  (the model needs it; the import is the check)
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(str(directory / TOKENIZER_DIRNAME))
        model = AutoModelForSequenceClassification.from_pretrained(
            str(directory / MODEL_DIRNAME),
            num_labels=len(self._labels),
        )
        model.to(device)
        model.eval()
        return model, tokenizer

    def _ensure_loaded(self) -> tuple[Any, Any]:
        if self._model is not None and self._tokenizer is not None:
            return self._model, self._tokenizer
        with self._lock:
            if self._model is not None and self._tokenizer is not None:
                return self._model, self._tokenizer
            if self._load_error is not None:
                raise self._load_error
            self._read_metadata()
            self.version = self._compute_version()
            if not self.artifact_exists():
                self._failure = "artifact_missing"
                self._load_error = ModelUnavailableError(
                    f"trained safety model not found at {self._directory} "
                    f"(expected {MODEL_DIRNAME}/ and {TOKENIZER_DIRNAME}/)"
                )
                raise self._load_error
            try:
                loader = self._loader or self._default_loader
                model, tokenizer = loader(self._directory, self._device, self._max_length)
                model.eval()
            except Exception as exc:  # any failure means "no model"
                # The exception text can carry paths and hub URLs; the type is
                # enough to diagnose, and nothing here is user text.
                self._failure = f"load_failed:{type(exc).__name__}"
                self._load_error = ModelUnavailableError(
                    f"trained safety model at {self._directory} could not be loaded"
                )
                raise self._load_error from exc
            self._model, self._tokenizer = model, tokenizer
            self._load_count += 1
            self._failure = "none"
            return self._model, self._tokenizer

    @property
    def failure(self) -> str:
        """Why the model is unavailable: ``none``, ``artifact_missing`` or a type name.

        A fixed vocabulary, never a path and never a message. This package has no
        logging call of its own, so this property is how the reason reaches
        whoever reads the classifier's provenance.
        """

        return self._failure

    # --- inference -----------------------------------------------------------

    def _probabilities(self, texts: Sequence[str]) -> list[dict[str, float]]:
        """Temperature-scaled softmax per text, as label -> probability."""

        model, tokenizer = self._ensure_loaded()
        import torch

        out: list[dict[str, float]] = []
        with torch.no_grad():
            for start in range(0, len(texts), self._batch_size):
                batch = [text for text in texts[start : start + self._batch_size]]
                encoded = tokenizer(
                    batch,
                    return_tensors="pt",
                    truncation=True,
                    max_length=self._max_length,
                    padding=True,
                )
                encoded = {key: value.to(self._device) for key, value in encoded.items()}
                logits = model(**encoded).logits / self._temperature
                probabilities = torch.softmax(logits, dim=-1).cpu().tolist()
                for row in probabilities:
                    out.append(
                        {
                            label: float(row[index]) if index < len(row) else 0.0
                            for index, label in enumerate(self._labels)
                        }
                    )
        return out

    def _prediction_for(self, probabilities: Mapping[str, float]) -> MLPrediction:
        mapping = {label: float(probabilities.get(label, 0.0)) for label in LEVEL_LABELS}

        # The point prediction. Ties break toward the *higher* level, exactly as
        # the Day-9 baseline does: a safety classifier has no business resolving
        # doubt downwards.
        best = max(mapping.values())
        level = RiskLevel.NONE
        for candidate in reversed(tuple(RiskLevel)):  # pragma: no branch
            if mapping[candidate.label] >= best - 1e-12:
                level = candidate
                break

        abstained = RiskLevel.NONE
        if self._abstain:
            levels = _prediction_set(mapping, self._quantile)
            abstained = _as_level(_abstain_level(levels, point=level.label))

        return MLPrediction(
            probabilities=mapping,
            level=level,
            confidence=mapping[level.label],
            version=self.version,
            abstained_level=abstained,
            prediction_set=(
                tuple(sorted(levels, key=_severity, reverse=True)) if self._abstain else ()
            ),
        )

    def predict(self, text: str) -> MLPrediction | None:
        """Score one message. ``None`` means "I cannot answer"."""

        if not text or not text.strip():
            return None
        probabilities = self._probabilities([text])[0]
        return self._prediction_for(probabilities)

    def predict_batch(self, texts: Sequence[str]) -> list[MLPrediction | None]:
        """Batch scoring — one forward pass per ``batch_size`` messages."""

        pending = [index for index, text in enumerate(texts) if text and text.strip()]
        results: list[MLPrediction | None] = [None] * len(texts)
        if not pending:
            return results
        probabilities = self._probabilities([texts[index] for index in pending])
        for position, index in enumerate(pending):
            results[index] = self._prediction_for(probabilities[position])
        return results


def build_trained_safety_classifier(
    *,
    artifact_dir: str = "",
    device: str = "cpu",
    max_length: int = 96,
    batch_size: int = 8,
    abstain: bool = True,
    loader: Callable[[Path, str, int], tuple[Any, Any]] | None = None,
) -> SafetyClassifier:
    """Load the packaged Day-33 model, or hand back a null classifier.

    Never raises and never blocks startup. A *constructed* classifier reports its
    own failures lazily (``TrainedSafetyClassifier.failure``, a fixed vocabulary);
    this factory only fails before construction, in which case it hands back a
    :class:`NullClassifier` whose ``reason`` is one of this module's words.

    Nothing here writes to a log sink: this package has no logging call anywhere
    (the privacy test greps the sources for one), and the reason travels in the
    classifier object instead.
    """

    from app.services.safety.ml_classifier import NullClassifier

    directory = Path(artifact_dir).expanduser() if artifact_dir.strip() else None
    if directory is None:
        return NullClassifier(reason="artifact_missing")
    try:
        return TrainedSafetyClassifier(
            directory,
            max_length=max_length,
            batch_size=batch_size,
            device=device,
            abstain=abstain,
            loader=loader,
        )
    except (ValueError, OSError):
        # Constructor guards only: a bad max_length or an unusable path.
        return NullClassifier(reason="artifact_invalid")
