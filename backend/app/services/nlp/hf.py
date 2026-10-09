"""Optional CPU-only Hugging Face multi-label adapter, serialized lazy initialization."""

from collections.abc import Callable
from importlib import import_module
from threading import Lock
from typing import Any

from app.services.nlp.base import Emotion, EmotionAnalyzer, EmotionResult, from_scores

# Group using max, not sum: independent sigmoid scores are not probabilities
# over a mutually exclusive taxonomy. Absence of a label never invents evidence.
LABEL_MAP: dict[str, Emotion] = {
    **dict.fromkeys(
        (
            "admiration",
            "amusement",
            "approval",
            "caring",
            "desire",
            "excitement",
            "gratitude",
            "joy",
            "love",
            "optimism",
            "pride",
        ),
        "joy",
    ),
    **dict.fromkeys(("disappointment", "grief", "sadness"), "sadness"),
    **dict.fromkeys(("anger", "annoyance", "disapproval", "disgust"), "anger"),
    "fear": "fear",
    "nervousness": "anxiety",
    "embarrassment": "shame",
    "remorse": "shame",
    "relief": "calm",
    "neutral": "neutral",
    "confusion": "neutral",
    "curiosity": "neutral",
    "realization": "neutral",
    "surprise": "neutral",
}


def load_pipeline(model_id: str) -> Any:
    # Fail before any hub lookup when the optional CPU runtime is absent.
    import_module("torch")
    pipeline = import_module("transformers").pipeline
    return pipeline("text-classification", model=model_id, device=-1, framework="pt")


class HFEmotionAnalyzer(EmotionAnalyzer):
    def __init__(
        self,
        model_id: str,
        *,
        max_length: int = 128,
        batch_size: int = 4,
        loader: Callable[[str], Any] = load_pipeline,
    ) -> None:
        self.model_id = model_id
        self.max_length = max_length
        self.batch_size = batch_size
        self._loader = loader
        self._pipeline: Any = None
        self._failed = False
        self._lock = Lock()

    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        return self.analyze_batch([text], lang)[0]

    def analyze_batch(self, texts: list[str], lang: str | None = None) -> list[EmotionResult]:
        if not texts:
            return []
        with self._lock:
            if self._failed:
                raise RuntimeError("emotion_model_unavailable")
            if self._pipeline is None:
                try:
                    self._pipeline = self._loader(self.model_id)
                except Exception:
                    self._failed = True
                    raise RuntimeError("emotion_model_load_failed") from None
            rows = self._pipeline(
                [text[:10000] for text in texts],
                truncation=True,
                max_length=self.max_length,
                batch_size=self.batch_size,
                top_k=None,
                function_to_apply="sigmoid",
            )
        if len(rows) != len(texts):
            raise ValueError("emotion_model_invalid_batch")
        results = []
        for row in rows:
            mapped: dict[Emotion, float] = {}
            for item in row:
                label = LABEL_MAP.get(str(item["label"]).lower())
                if label is not None:
                    mapped[label] = max(mapped.get(label, 0.0), float(item["score"]))
            if not mapped:
                raise ValueError("emotion_model_unknown_labels")
            results.append(from_scores(mapped))
        return results
