"""The emotion-analysis contract, the internal taxonomy, and the result shape.

Every analyzer in this package returns an :class:`EmotionResult`. The four
required fields are ``primary`` / ``scores`` / ``valence`` / ``arousal``; the
rest is provenance so a caller (or a log line) can tell *which* analyzer
produced the answer and how much to trust it.

Two design decisions worth stating here because they are easy to get wrong:

* ``scores`` is always a **distribution over the internal taxonomy** (values in
  ``[0, 1]``, summing to 1.0), even when the model underneath is multi-label and
  its raw outputs do not sum to one. The largest raw probability is preserved
  separately as ``confidence`` so "the model was sure" and "the model was not
  sure but joy was the least-bad label" stay distinguishable.
* ``valence`` and ``arousal`` are never invented by an analyzer. They are
  derived from :data:`EMOTION_DIMENSIONS` - one hand-set (valence, arousal)
  anchor per label - so the numbers mean the same thing no matter which
  analyzer answered, and a label change is one table edit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

#: The internal taxonomy. Small on purpose: downstream features (mood tracking,
#: journal summaries, exercise selection) cannot act on 27 GoEmotions labels,
#: and a taxonomy a human can reason about is a taxonomy that can be audited.
EMOTIONS: tuple[str, ...] = (
    "joy",
    "sadness",
    "anger",
    "fear",
    "anxiety",
    "shame",
    "loneliness",
    "calm",
    "neutral",
)

NEUTRAL: str = "neutral"

#: ``(valence, arousal)`` anchors, valence in ``[-1, 1]`` (unpleasant..pleasant)
#: and arousal in ``[0, 1]`` (calm..activated). A rough circumplex placement:
#: fear and anger are negative *and* high-arousal, sadness and shame negative
#: and low-arousal, calm positive and low-arousal.
EMOTION_DIMENSIONS: dict[str, tuple[float, float]] = {
    "joy": (0.8, 0.6),
    "sadness": (-0.7, 0.25),
    "anger": (-0.6, 0.7),
    "fear": (-0.65, 0.75),
    "anxiety": (-0.5, 0.6),
    "shame": (-0.55, 0.35),
    "loneliness": (-0.6, 0.3),
    "calm": (0.4, 0.2),
    "neutral": (0.0, 0.3),
}


class EmotionResult(BaseModel):
    """What an analyzer knows about one piece of text.

    Validated hard: an out-of-range valence or an unknown label reaching a
    downstream feature is a worse failure than an exception at the boundary.
    """

    primary: str = Field(description="Highest-scoring emotion in the internal taxonomy.")
    scores: dict[str, float] = Field(description="Distribution over the internal taxonomy.")
    valence: float = Field(description="Unpleasant (-1) .. pleasant (+1).")
    arousal: float = Field(description="Calm (0) .. activated (1).")

    # --- Provenance (never used for scoring, always used for debugging) ---
    analyzer: str = Field(description="Which analyzer produced this (hf, keyword, fake, ...).")
    model: str | None = Field(default=None, description="Model id, if a model was involved.")
    language: str | None = Field(default=None, description="Language code the text was read as.")
    truncated: bool = Field(default=False, description="Input exceeded the analyzer's window.")
    confidence: float = Field(default=1.0, description="Largest raw label probability, 0..1.")
    cached: bool = Field(default=False, description="Served from the analysis cache.")

    @field_validator("primary")
    @classmethod
    def _primary_is_known(cls, value: str) -> str:
        if value not in EMOTIONS:
            raise ValueError(f"unknown emotion {value!r}; expected one of {', '.join(EMOTIONS)}")
        return value

    @field_validator("scores")
    @classmethod
    def _scores_are_valid(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("scores must not be empty")
        for label, score in value.items():
            if label not in EMOTIONS:
                raise ValueError(f"unknown emotion label {label!r}")
            if not 0.0 <= score <= 1.0:
                raise ValueError(f"score for {label!r} must be within [0, 1], got {score}")
        return value

    @field_validator("valence")
    @classmethod
    def _valence_in_range(cls, value: float) -> float:
        if not -1.0 <= value <= 1.0:
            raise ValueError(f"valence must be within [-1, 1], got {value}")
        return value

    @field_validator("arousal")
    @classmethod
    def _arousal_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"arousal must be within [0, 1], got {value}")
        return value

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"confidence must be within [0, 1], got {value}")
        return value

    @model_validator(mode="after")
    def _primary_is_the_top_score(self) -> EmotionResult:
        """``primary`` must agree with ``scores`` - a mismatch is a bug."""
        top = max(self.scores, key=lambda label: self.scores[label])
        if abs(self.scores[top] - self.scores[self.primary]) > 1e-9:
            raise ValueError(f"primary {self.primary!r} is not the highest score")
        return self

    @property
    def intensity(self) -> float:
        """How much of the distribution sits on the primary label (0..1)."""
        return self.scores[self.primary]

    @property
    def is_informative(self) -> bool:
        """Whether this result is an opinion or just an absence of one.

        A zero-confidence ``neutral`` means "the analyzer looked and found
        nothing", which is exactly the case where the next analyzer in the
        chain should get a turn. A confident ``neutral`` (a model that
        considered the text and called it flat) is a real answer and stops the
        chain.
        """
        return self.primary != NEUTRAL or self.confidence > 0.0


def normalize_scores(mapped: Mapping[str, float]) -> dict[str, float]:
    """Turn arbitrary per-label weights into a distribution over the taxonomy.

    Every taxonomy label is present (zeros included) so callers can index
    without a ``KeyError``, and values are rounded to 6 places to keep
    comparisons and cached JSON stable.
    """
    weights = {label: max(0.0, float(mapped.get(label, 0.0))) for label in EMOTIONS}
    total = sum(weights.values())
    if total <= 0.0:
        return {label: (1.0 if label == NEUTRAL else 0.0) for label in EMOTIONS}
    return {label: round(weight / total, 6) for label, weight in weights.items()}


def dimensions_for(scores: Mapping[str, float]) -> tuple[float, float]:
    """Weighted ``(valence, arousal)`` over a score distribution."""
    valence = sum(score * EMOTION_DIMENSIONS[label][0] for label, score in scores.items())
    arousal = sum(score * EMOTION_DIMENSIONS[label][1] for label, score in scores.items())
    return round(max(-1.0, min(1.0, valence)), 6), round(max(0.0, min(1.0, arousal)), 6)


def build_result(
    mapped: Mapping[str, float],
    *,
    analyzer: str,
    model: str | None = None,
    language: str | None = None,
    truncated: bool = False,
    confidence: float | None = None,
) -> EmotionResult:
    """Build a validated result from raw per-label weights."""
    scores = normalize_scores(mapped)
    valence, arousal = dimensions_for(scores)
    primary = max(EMOTIONS, key=lambda label: scores[label])
    resolved_confidence = confidence
    if resolved_confidence is None:
        resolved_confidence = max(mapped.values()) if mapped else 0.0
    return EmotionResult(
        primary=primary,
        scores=scores,
        valence=valence,
        arousal=arousal,
        analyzer=analyzer,
        model=model,
        language=language,
        truncated=truncated,
        confidence=round(max(0.0, min(1.0, resolved_confidence)), 6),
    )


def neutral_result(
    *,
    analyzer: str,
    model: str | None = None,
    language: str | None = None,
    truncated: bool = False,
) -> EmotionResult:
    """The "nothing to say" result: neutral, valence 0, no confidence."""
    return EmotionResult(
        primary=NEUTRAL,
        scores=normalize_scores({}),
        valence=0.0,
        arousal=EMOTION_DIMENSIONS[NEUTRAL][1],
        analyzer=analyzer,
        model=model,
        language=language,
        truncated=truncated,
        confidence=0.0,
    )


class EmotionAnalyzer(ABC):
    """The interface every emotion source implements.

    ``analyze`` is deliberately **synchronous**: the Hugging Face pipeline is a
    blocking CPU call, so callers that must not block an event loop (the dev
    endpoint) run it through ``run_in_threadpool``. Implementations must be
    safe to call from several threads at once.
    """

    #: Short stable name, used in responses, logs and the analysis cache key.
    #: A plain attribute, not a ClassVar: production analyzers set it per
    #: class, but a test double needs to set it per instance.
    name: str = "abstract"

    @abstractmethod
    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        """Analyze one string. Never raises for empty or odd input."""

    def analyze_many(self, texts: Sequence[str], lang: str | None = None) -> list[EmotionResult]:
        """Analyze a batch. The default implementation loops; models override."""
        return [self.analyze(text, lang) for text in texts]

    @property
    def model_id(self) -> str | None:
        """The model behind this analyzer, if any (``None`` for lexicons)."""
        return None

    def describe(self) -> dict[str, Any]:
        """Non-sensitive metadata for logs and the dev endpoint."""
        return {"analyzer": self.name, "model": self.model_id}

    def close(self) -> None:
        """Release any resources held by this analyzer.

        Optional: the default is a no-op, overridden by the model-backed
        analyzer, which owns a loaded pipeline.
        """
        return None
