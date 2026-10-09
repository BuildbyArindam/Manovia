"""Hugging Face emotion analysis: lazy, thread-safe, CPU-friendly.

The model is whatever ``EMOTION_MODEL_ID`` names, so the choice is a
configuration decision, not a code change (see
``docs/adr/0006-emotion-model.md`` for the default, the alternatives and the
licence question). Whatever it is, its label set is almost certainly *not* the
nine labels the rest of Manovia speaks, so :data:`LABEL_MAP` folds it down.

Everything here is defensive because the model is the least reliable part of
the stack:

* **lazy + thread-safe.** The first call loads it, under a lock, exactly once;
  later calls reuse it. A failed load is remembered, so a broken model id costs
  one warning and one failed import, not one per request.
* **CPU-friendly.** ``device=-1`` (CPU), ``torch`` is never asked for a GPU,
  batches are small, and long input is pre-cut before tokenising.
* **truncation.** ``truncation=True`` + ``max_length`` at the pipeline, and a
  character-level pre-cut so a 10,000-character message cannot blow up
  tokenisation time or memory.
* **never fatal.** :class:`ModelUnavailableError` is the only exception this
  module raises, and the chain around it treats that as "use the fallback".

Importantly, ``transformers``/``torch`` are imported **inside** the loader. The
offline test suite, the CI runner and any deployment that has not installed the
``nlp`` extra must be able to import this module and get the fallback.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

import structlog

from app.services.nlp.base import (
    EMOTIONS,
    NEUTRAL,
    EmotionAnalyzer,
    EmotionResult,
    build_result,
    neutral_result,
)


class ModelUnavailableError(RuntimeError):
    """The model could not be loaded, or failed while running."""


#: Model label (lowercased) → ``(internal emotion, weight)``.
#:
#: Covers the two label sets the default model ids use - GoEmotions (27 labels)
#: and the Ekman set (7 labels) - plus the ``positive``/``negative`` pair that
#: sentiment-flavoured emotion models emit. Weights below 1.0 mean "this label
#: implies the emotion, but partly": ``disgust`` is anger-ish, ``relief`` is
#: calm-ish, ``confusion`` is mostly just neutral.
LABEL_MAP: Final[dict[str, tuple[str, float]]] = {
    # --- Ekman / basic emotion models ---
    "joy": ("joy", 1.0),
    "happiness": ("joy", 1.0),
    "sadness": ("sadness", 1.0),
    "sad": ("sadness", 1.0),
    "anger": ("anger", 1.0),
    "angry": ("anger", 1.0),
    "fear": ("fear", 1.0),
    "afraid": ("fear", 1.0),
    "disgust": ("anger", 0.8),
    "surprise": (NEUTRAL, 0.4),
    "neutral": (NEUTRAL, 1.0),
    # --- GoEmotions (27) ---
    "admiration": ("joy", 0.6),
    "amusement": ("joy", 1.0),
    "annoyance": ("anger", 0.6),
    "approval": ("joy", 0.5),
    "caring": ("joy", 0.5),
    "confusion": (NEUTRAL, 0.4),
    "curiosity": (NEUTRAL, 0.5),
    "desire": ("joy", 0.5),
    "disappointment": ("sadness", 0.8),
    "disapproval": ("anger", 0.7),
    "embarrassment": ("shame", 1.0),
    "excitement": ("joy", 0.9),
    "gratitude": ("joy", 0.7),
    "grief": ("sadness", 1.0),
    "love": ("joy", 0.8),
    "nervousness": ("anxiety", 1.0),
    "optimism": ("joy", 0.6),
    "pride": ("joy", 0.7),
    "realization": (NEUTRAL, 0.3),
    "relief": ("calm", 0.9),
    "remorse": ("shame", 0.9),
    # --- sentiment-flavoured emotion models ---
    "positive": ("joy", 0.8),
    "negative": ("sadness", 0.7),
    # --- common alternates for the taxonomy's harder labels ---
    "anxiety": ("anxiety", 1.0),
    "anxious": ("anxiety", 1.0),
    "worry": ("anxiety", 0.9),
    "shame": ("shame", 1.0),
    "guilt": ("shame", 0.9),
    "loneliness": ("loneliness", 1.0),
    "lonely": ("loneliness", 1.0),
    "isolation": ("loneliness", 0.9),
    "calm": ("calm", 1.0),
    "contentment": ("calm", 0.8),
    "serenity": ("calm", 0.9),
    "relaxed": ("calm", 0.9),
}

#: Prefix some checkpoints leave on un-renamed heads.
_LABEL_PREFIXES: Final[tuple[str, ...]] = ("label_", "label-")

# Characters per token assumed when pre-cutting input. Generous (English is
# ~4): the tokenizer still truncates properly, this only bounds the work.
CHARS_PER_TOKEN: Final[int] = 4


def normalize_label(label: str) -> str:
    """Lowercase a model label and strip a ``LABEL_n`` prefix."""
    cleaned = str(label).strip().casefold()
    for prefix in _LABEL_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned.removeprefix(prefix)
    return cleaned.replace("_", " ").strip()


def map_labels(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, float], float, list[str]]:
    """Fold one text's raw ``[{label, score}, ...]`` into the internal taxonomy.

    Returns ``(weights, confidence, unknown_labels)``. Per internal emotion the
    **maximum** mapped probability is used rather than the sum: a multi-label
    model can fire on several labels of one family ("sadness" *and* "grief"),
    and summing those would invent confidence the model never expressed.
    """
    weights: dict[str, float] = {emotion: 0.0 for emotion in EMOTIONS}
    unknown: list[str] = []
    confidence = 0.0
    for row in rows:
        raw_label = row.get("label", "")
        score = row.get("score", 0.0)
        try:
            probability = max(0.0, min(1.0, float(score)))
        except (TypeError, ValueError):
            continue
        confidence = max(confidence, probability)
        mapped = LABEL_MAP.get(normalize_label(str(raw_label)))
        if mapped is None:
            unknown.append(str(raw_label))
            continue
        emotion, weight = mapped
        weights[emotion] = max(weights[emotion], probability * weight)
    return weights, confidence, unknown


class HFEmotionAnalyzer(EmotionAnalyzer):
    """A multi-label Hugging Face text-classification model, loaded once."""

    name = "hf"

    def __init__(
        self,
        model_id: str,
        *,
        max_length: int = 256,
        batch_size: int = 8,
        device: str = "cpu",
        pipeline_factory: Callable[..., Any] | None = None,
        tokenizer_kwarg_support: bool = True,
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id must not be empty")
        if max_length < 8:
            raise ValueError("max_length must be at least 8 tokens")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._model_id = model_id.strip()
        self._max_length = max_length
        self._batch_size = batch_size
        self._device = device
        self._pipeline_factory = pipeline_factory
        self._tokenizer_kwarg_support = tokenizer_kwarg_support
        self._pipeline: Any | None = None
        self._load_error: ModelUnavailableError | None = None
        self._lock = threading.Lock()
        self._load_count = 0
        self._warned_unknown: set[str] = set()

    @property
    def model_id(self) -> str | None:
        return self._model_id

    @property
    def max_length(self) -> int:
        return self._max_length

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def load_count(self) -> int:
        """How many times the model was actually loaded (tests: must stay 1)."""
        return self._load_count

    @property
    def is_loaded(self) -> bool:
        return self._pipeline is not None

    @property
    def is_warm(self) -> bool:
        """False until the pipeline exists, so the load is not timed as
        inference."""
        return self._pipeline is not None

    # --- loading -----------------------------------------------------------

    def _build_pipeline(self) -> Any:
        """Create the pipeline. Imports happen here, not at module import."""
        if self._pipeline_factory is not None:
            factory: Callable[..., Any] = self._pipeline_factory
        else:  # pragma: no cover - only when transformers is installed
            from transformers import pipeline as factory  # type: ignore[no-redef]

        kwargs: dict[str, Any] = {
            "model": self._model_id,
            # -1 is CPU in the transformers device convention; a string is only
            # understood by newer releases, and CPU is the supported target.
            "device": -1 if self._device.strip().lower() == "cpu" else self._device,
            "truncation": True,
            "max_length": self._max_length,
            # Multi-label: every label's probability, not just the argmax.
            "top_k": None,
        }
        return factory("text-classification", **kwargs)

    def _ensure_loaded(self) -> Any:
        """Load the pipeline once, thread-safely, and remember failures."""
        pipeline = self._pipeline
        if pipeline is not None:
            return pipeline
        with self._lock:
            # Double-checked: another thread may have finished the load.
            if self._pipeline is not None:
                return self._pipeline
            if self._load_error is not None:
                raise self._load_error
            try:
                self._pipeline = self._build_pipeline()
                self._load_count += 1
            except Exception as exc:  # any failure means "no model"
                # The exception text can carry paths and hub URLs; the type is
                # enough to diagnose, and nothing here is user text.
                self._load_error = ModelUnavailableError(
                    f"emotion model {self._model_id!r} could not be loaded"
                )
                structlog.get_logger().warning(
                    "emotion_model_unavailable",
                    model_id=self._model_id,
                    error_type=type(exc).__name__,
                    fallback="lexicon",
                )
                raise self._load_error from exc
            structlog.get_logger().info(
                "emotion_model_loaded",
                model_id=self._model_id,
                max_length=self._max_length,
                batch_size=self._batch_size,
            )
            return self._pipeline

    def try_load(self) -> bool:
        """Attempt the load now; ``False`` (plus a warning) if it fails."""
        try:
            self._ensure_loaded()
        except ModelUnavailableError:
            return False
        return True

    # --- analysis ----------------------------------------------------------

    def _pre_cut(self, text: str) -> tuple[str, bool]:
        """Bound the characters handed to the tokenizer.

        ``truncation=True`` already caps tokens; this caps the *work* of
        tokenising a very long message first, which is what keeps a
        10,000-character paste cheap.
        """
        limit = self._max_length * CHARS_PER_TOKEN
        if len(text) <= limit:
            return text, False
        return text[:limit], True

    def _run(self, texts: Sequence[str]) -> list[list[dict[str, Any]]]:
        pipeline = self._ensure_loaded()
        results: list[list[dict[str, Any]]] = []
        for start in range(0, len(texts), self._batch_size):
            chunk = list(texts[start : start + self._batch_size])
            try:
                raw = pipeline(chunk)
            except Exception as exc:  # inference failures degrade
                structlog.get_logger().warning(
                    "emotion_model_inference_failed",
                    model_id=self._model_id,
                    error_type=type(exc).__name__,
                    batch_size=len(chunk),
                )
                raise ModelUnavailableError("emotion model inference failed") from exc
            results.extend(self._normalize_batch(raw, len(chunk)))
        return results

    @staticmethod
    def _normalize_batch(raw: Any, expected: int) -> list[list[dict[str, Any]]]:
        """Coerce pipeline output to one list of ``{label, score}`` per text.

        ``top_k=None`` yields a list of lists, but a single-label checkpoint
        (``top_k=1``) yields a list of dicts, and one text yields a bare list.
        """
        if expected == 1 and isinstance(raw, dict):
            return [[dict(raw)]]
        if expected == 1 and raw and isinstance(raw[0], dict):
            return [[dict(row) for row in raw]]
        rows: list[list[dict[str, Any]]] = []
        for item in raw:
            if isinstance(item, dict):
                rows.append([dict(item)])
            else:
                rows.append([dict(row) for row in item])
        return rows

    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        return self.analyze_many([text], lang)[0]

    def analyze_many(self, texts: Sequence[str], lang: str | None = None) -> list[EmotionResult]:
        """Analyze a batch. Empty strings short-circuit to ``neutral``."""
        pending: list[tuple[int, str, bool]] = []
        results: list[EmotionResult | None] = [None] * len(texts)
        for index, text in enumerate(texts):
            if not text or not text.strip():
                results[index] = neutral_result(
                    analyzer=self.name, model=self._model_id, language=lang
                )
                continue
            cut, pre_cut = self._pre_cut(text)
            pending.append((index, cut, pre_cut))

        if pending:
            rows = self._run([item[1] for item in pending])
            for (index, original, pre_cut), labels in zip(pending, rows, strict=True):
                weights, confidence, unknown = map_labels(labels)
                for label in unknown:
                    if label not in self._warned_unknown:
                        self._warned_unknown.add(label)
                        structlog.get_logger().warning(
                            "emotion_label_unmapped",
                            model_id=self._model_id,
                            label=label,
                        )
                results[index] = build_result(
                    weights,
                    analyzer=self.name,
                    model=self._model_id,
                    language=lang,
                    # Conservative: a message longer than the token budget in
                    # characters certainly lost its tail; a shorter one may
                    # still have been truncated at a token boundary.
                    truncated=pre_cut or len(original) > self._max_length,
                    confidence=confidence,
                )

        return [
            result if result is not None else neutral_result(analyzer=self.name)
            for result in results
        ]

    def close(self) -> None:
        """Drop the loaded pipeline so memory is released."""
        with self._lock:
            self._pipeline = None
            self._load_error = None
