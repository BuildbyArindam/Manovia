"""Emotion contract and deliberately approximate dimensional projections."""

from abc import ABC, abstractmethod
from typing import Annotated, Literal

from pydantic import BaseModel, Field

Emotion = Literal[
    "joy", "sadness", "anger", "fear", "anxiety", "shame", "loneliness", "calm", "neutral"
]
TAXONOMY: tuple[Emotion, ...] = (
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
VALENCE = dict(zip(TAXONOMY, (0.9, -0.8, -0.7, -0.8, -0.6, -0.7, -0.7, 0.3, 0.0), strict=True))
AROUSAL = dict(zip(TAXONOMY, (0.7, 0.3, 0.9, 0.9, 0.8, 0.4, 0.3, 0.1, 0.2), strict=True))


class EmotionResult(BaseModel):
    primary: Emotion
    scores: dict[Emotion, Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]]
    valence: float = Field(ge=-1, le=1, allow_inf_nan=False)
    arousal: float = Field(ge=0, le=1, allow_inf_nan=False)


def from_scores(raw: dict[Emotion, float]) -> EmotionResult:
    scores = {label: max(0.0, min(1.0, raw.get(label, 0.0))) for label in TAXONOMY}
    if not any(scores.values()):
        scores["neutral"] = 1.0
    total = sum(scores.values())
    return EmotionResult(
        primary=max(scores, key=lambda label: scores[label]),
        scores=scores,
        valence=sum(VALENCE[label] * score for label, score in scores.items()) / total,
        arousal=sum(AROUSAL[label] * score for label, score in scores.items()) / total,
    )


class EmotionAnalyzer(ABC):
    @abstractmethod
    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        """Analyze without logging or persisting text; not a clinical assessment."""

    def analyze_batch(self, texts: list[str], lang: str | None = None) -> list[EmotionResult]:
        return [self.analyze(text, lang) for text in texts]
