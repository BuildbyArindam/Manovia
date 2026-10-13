"""The fine-tuned multilingual emotion model, behind the existing interface.

``EMOTION_ANALYZER=trained`` leads the chain with the LoRA model from Day 32
(`ml/src/manovia_ml/training/emotion/`), packaged as a directory:

| file | what it is |
| --- | --- |
| ``model/`` | merged weights (no PEFT adapter to load) + config |
| ``tokenizer/`` | the tokenizer that was trained with it |
| ``thresholds.json`` | per-label thresholds tuned on the *validation* split |
| ``manifest.json`` | base model, labels, data hashes, run id, library versions |
| ``MODEL_CARD.md`` | the card, including the limitations |

Design rules, all of them inherited from the Day-6 analyzer and the chain:

* **Lazy and thread-safe.** First use loads, exactly once, under a lock. A failed
  load is remembered, so a missing artefact directory costs one warning and one
  failed attempt rather than one per request.
* **Never fatal.** A missing directory, a missing ``transformers``/``torch``
  install, or an exception mid-inference becomes :class:`ModelUnavailableError`,
  which the chain turns into the lexicon fallback. Deleting the weights is a
  supported state, not an outage.
* **The schema is unchanged.** The result is an :class:`EmotionResult` with the
  same nine-label ``scores`` distribution, ``valence``/``arousal`` derived from
  the product's own anchors, and the raw maximum probability in ``confidence``.
  ``primary`` remains the argmax, because the result schema enforces exactly that
  (``base.EmotionResult._primary_is_the_top_score``) — so the tuned thresholds
  are exposed through :meth:`TrainedEmotionAnalyzer.asserted_labels`, which is
  what the evaluation uses, instead of silently rescaling a distribution to make
  a label win.
* **No safety role.** Like every other analyzer here it is a tone hint; it cannot
  lower a risk level and is never called at HIGH or IMMINENT risk (ADR 0009).

Nothing here computes; the model does. This module's job is to be the *only*
place that knows the artefact layout, so a layout change is one file.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import structlog

from app.services.nlp.base import (
    EMOTIONS,
    NEUTRAL,
    EmotionAnalyzer,
    EmotionResult,
    build_result,
    neutral_result,
)
from app.services.nlp.hf import ModelUnavailableError

__all__ = ["ARTIFACT_FILES", "TrainedEmotionAnalyzer"]

#: The layout, in one place. The ML package writes exactly these
#: (`manovia_ml.training.emotion.model`); the test suite checks the two agree.
MODEL_DIRNAME = "model"
TOKENIZER_DIRNAME = "tokenizer"
THRESHOLDS_FILENAME = "thresholds.json"
MANIFEST_FILENAME = "manifest.json"
CARD_FILENAME = "MODEL_CARD.md"

ARTIFACT_FILES: tuple[str, ...] = (
    MODEL_DIRNAME,
    TOKENIZER_DIRNAME,
    THRESHOLDS_FILENAME,
    MANIFEST_FILENAME,
    CARD_FILENAME,
)

#: Threshold used for a label the artefact has no tuned value for. Deliberately
#: the same 0.5 the ML package falls back to.
DEFAULT_THRESHOLD = 0.5


class TrainedEmotionAnalyzer(EmotionAnalyzer):
    """A packaged fine-tuned model, loaded on first use and never fatal."""

    name = "trained"

    def __init__(
        self,
        directory: str | Path,
        *,
        max_length: int = 128,
        batch_size: int = 8,
        device: str = "cpu",
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
        self._loader = loader
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._labels: tuple[str, ...] = EMOTIONS
        self._thresholds: dict[str, float] = {}
        self._manifest: dict[str, Any] = {}
        self._load_error: ModelUnavailableError | None = None
        self._lock = threading.Lock()
        self._load_count = 0
        self._warned_missing = False

    # --- introspection -------------------------------------------------------

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def model_id(self) -> str | None:
        """The MLflow run id when the manifest has one, else the directory name."""

        run_id = self._manifest.get("run_id")
        if isinstance(run_id, str) and run_id:
            return run_id
        return self._manifest.get("base_model") or self._directory.name

    @property
    def thresholds(self) -> dict[str, float]:
        return dict(self._thresholds)

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

    @property
    def is_warm(self) -> bool:
        """False until the weights exist, so the chain does not time the load."""

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
            "thresholds": {
                label: round(value, 4) for label, value in sorted(self._thresholds.items())
            },
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
                    self._labels = tuple(str(label) for label in labels)

        thresholds_path = self._directory / THRESHOLDS_FILENAME
        if thresholds_path.is_file():
            try:
                payload = json.loads(thresholds_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            raw = payload.get("thresholds", payload) if isinstance(payload, dict) else {}
            if isinstance(raw, Mapping):
                self._thresholds = {str(label): float(value) for label, value in raw.items()}

    def _default_loader(self, directory: Path, device: str, max_length: int) -> tuple[Any, Any]:
        """Import transformers/torch here: the offline suite must import this module."""

        import torch  # noqa: F401  (the model needs it; the import is the check)
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(str(directory / TOKENIZER_DIRNAME))
        model = AutoModelForSequenceClassification.from_pretrained(
            str(directory / MODEL_DIRNAME),
            num_labels=len(self._labels),
            problem_type="multi_label_classification",
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
            if not self.artifact_exists():
                self._load_error = ModelUnavailableError(
                    f"trained emotion model not found at {self._directory} "
                    f"(expected {MODEL_DIRNAME}/ and {TOKENIZER_DIRNAME}/)"
                )
                self._warn_once("trained_emotion_model_missing")
                raise self._load_error
            try:
                loader = self._loader or self._default_loader
                model, tokenizer = loader(self._directory, self._device, self._max_length)
                model.eval()
            except Exception as exc:  # any failure means "no model"
                self._load_error = ModelUnavailableError(
                    f"trained emotion model at {self._directory} could not be loaded"
                )
                structlog.get_logger().warning(
                    "trained_emotion_model_unavailable",
                    directory=str(self._directory),
                    error_type=type(exc).__name__,
                    fallback="lexicon",
                )
                raise self._load_error from exc
            self._model, self._tokenizer = model, tokenizer
            self._load_count += 1
            structlog.get_logger().info(
                "trained_emotion_model_loaded",
                directory=str(self._directory),
                base_model=self._manifest.get("base_model"),
                thresholds=len(self._thresholds),
            )
            return self._model, self._tokenizer

    def _warn_once(self, event: str) -> None:
        if not self._warned_missing:
            self._warned_missing = True
            structlog.get_logger().warning(
                event, directory=str(self._directory), fallback="lexicon"
            )

    # --- inference -----------------------------------------------------------

    def _probabilities(self, texts: Sequence[str]) -> list[list[float]]:
        model, tokenizer = self._ensure_loaded()
        import torch

        outputs: list[list[float]] = []
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
                logits = model(**encoded).logits
                outputs.extend(torch.sigmoid(logits).cpu().tolist())
        return outputs

    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        """One text. Empty input is neutral; every failure is a fallback, not a raise."""

        if not text or not text.strip():
            return neutral_result(analyzer=self.name, model=self.model_id, language=lang)
        probabilities = self._probabilities([text])[0]
        return self._result_for(probabilities, lang=lang, truncated=self._is_truncated(text))

    def analyze_many(self, texts: Sequence[str], lang: str | None = None) -> list[EmotionResult]:
        """Batch inference — one forward pass per ``batch_size`` texts."""

        texts = list(texts)
        if not texts:
            return []
        results: list[EmotionResult | None] = [None] * len(texts)
        pending: list[int] = []
        for index, text in enumerate(texts):
            if not text or not text.strip():
                results[index] = neutral_result(
                    analyzer=self.name, model=self.model_id, language=lang
                )
            else:
                pending.append(index)
        if pending:
            probabilities = self._probabilities([texts[index] for index in pending])
            for position, index in enumerate(pending):
                results[index] = self._result_for(
                    probabilities[position], lang=lang, truncated=self._is_truncated(texts[index])
                )
        return [result for result in results if result is not None]

    def _is_truncated(self, text: str) -> bool:
        """A cheap pre-check, like the Hugging Face analyzer's character cut."""

        return len(text) > self._max_length * 4

    def _result_for(
        self,
        probabilities: Sequence[float],
        *,
        lang: str | None,
        truncated: bool,
    ) -> EmotionResult:
        labels = self._labels
        mapped = {
            label: float(probabilities[index])
            for index, label in enumerate(labels)
            if index < len(probabilities)
        }
        return build_result(
            mapped,
            analyzer=self.name,
            model=self.model_id,
            language=lang,
            truncated=truncated,
            confidence=max(mapped.values()) if mapped else 0.0,
        )

    def asserted_labels(self, text: str) -> frozenset[str]:
        """The labels whose tuned threshold the model clears.

        This is where the per-label thresholds are used on the product side. The
        result schema cannot carry them (``primary`` is defined as the argmax of
        ``scores``), so a caller that wants "which emotions does this model
        actually claim" asks here instead of reading a rescaled distribution.
        """

        if not text or not text.strip():
            return frozenset()
        probabilities = self._probabilities([text])[0]
        return frozenset(
            label
            for index, label in enumerate(self._labels)
            if index < len(probabilities)
            and float(probabilities[index]) >= self._thresholds.get(label, DEFAULT_THRESHOLD)
        )

    def top_label(self, text: str) -> str:
        """Shortcut for the argmax label, used by tests and diagnostics."""

        if not text or not text.strip():
            return NEUTRAL
        probabilities = self._probabilities([text])[0]
        return self._labels[max(range(len(self._labels)), key=lambda index: probabilities[index])]
