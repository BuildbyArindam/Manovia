"""The two baselines the fine-tuned model has to beat, measured with one harness.

| baseline | what it is | needs |
| --- | --- | --- |
| ``keyword`` | the shipped lexicon chain (`KeywordFallbackAnalyzer`, Day 6), the
  path that answers in the container today | the backend importable; no network |
| ``sentiment`` | the polarity scorer, the chain's second link | same |
| ``baseline`` | the off-the-shelf checkpoint the product points at today
  (`EMOTION_MODEL_ID`, `SamLowe/roberta-base-go_emotions`), mapped through the
  product's own ``LABEL_MAP`` | the backend **and** the `nlp` extra **and** a model download |

The three run through the *same* scoring code as the trained model
(:mod:`manovia_ml.training.emotion.evaluate`), so the comparison is not a
comparison of two harnesses. What the baselines do not get, because they cannot
have it, is a tuned threshold: their operating point is the product's own
(scores ≥ 0.5, primary = argmax), and the report says so next to the numbers.

The backend import is done the way every other cross-project script in this
repository does it (``evals/*.py``): put the checkout on ``sys.path``. `ml/` must
stay installable without the backend, so this module imports nothing from ``app``
at import time — only inside the runner functions, and only when a config asks
for that baseline.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manovia_ml.paths import ml_root
from manovia_ml.training.emotion.data import EmotionExample
from manovia_ml.training.emotion.taxonomy import EMOTIONS

__all__ = ["BASELINE_MODEL_ID", "BaselineResult", "available_baselines", "run_baseline"]

#: The checkpoint the product points at today (``EMOTION_MODEL_ID`` in the
#: backend settings). Named once here so the availability check and the runner
#: cannot drift apart — they did: the check reported "runnable" from an
#: importability test and the runner then died on a download.
BASELINE_MODEL_ID = "SamLowe/roberta-base-go_emotions"

#: Anything whose score reaches this is a positive for a baseline. The trained
#: model's thresholds are tuned for *its* calibration; applying them to a
#: different scorer would be a second, unmeasured experiment.
BASELINE_THRESHOLD = 0.5


@dataclass(frozen=True)
class BaselineResult:
    """A baseline's per-label probabilities plus the labels they belong to."""

    name: str
    labels: tuple[str, ...]
    probabilities: list[list[float]]
    notes: str = ""

    def predict_sets(self, threshold: float = BASELINE_THRESHOLD) -> list[frozenset[str]]:
        return [
            frozenset(
                label for index, label in enumerate(self.labels) if float(row[index]) >= threshold
            )
            for row in self.probabilities
        ]


def _backend_on_path() -> Path:
    backend = ml_root().parent / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    return backend


def available_baselines() -> dict[str, str]:
    """``{name: reason}`` — empty reason means "this one can run here"."""

    out: dict[str, str] = {}
    try:
        _backend_on_path()
        from app.services.nlp.keyword import KeywordFallbackAnalyzer  # noqa: F401
        from app.services.nlp.sentiment import SentimentAnalyzer  # noqa: F401

        out["keyword"] = ""
        out["sentiment"] = ""
    except Exception as exc:  # pragma: no cover - depends on the checkout layout
        out["keyword"] = f"backend not importable: {type(exc).__name__}"
        out["sentiment"] = out["keyword"]

    try:
        import transformers  # noqa: F401
        from app.services.nlp.hf import HFEmotionAnalyzer  # noqa: F401
    except Exception as exc:
        out["baseline"] = (
            f"off-the-shelf checkpoint needs the `nlp` extra and a model download "
            f"({type(exc).__name__})"
        )
        return out

    out["baseline"] = checkpoint_is_cached()
    return out


def checkpoint_is_cached(model_id: str = BASELINE_MODEL_ID) -> str:
    """``""`` when the off-the-shelf checkpoint is on this machine, else why not.

    Being importable is not the same as being *runnable*: the weights are a
    network download, and the whole `ml/` tree is built to work offline (CI,
    the sandbox, a machine on a plane). Asking the Hugging Face cache first
    turns "the report crashed halfway through" into one honest line in the
    report — which is the difference between a limitation and a bug.
    """

    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError as exc:  # pragma: no cover - depends on the extra
        return f"off-the-shelf checkpoint needs `huggingface_hub` ({type(exc).__name__})"

    try:
        cached = try_to_load_from_cache(model_id, "config.json")
    except Exception as exc:  # pragma: no cover - depends on the cache layout
        return f"off-the-shelf checkpoint {model_id}: cache lookup failed ({type(exc).__name__})"

    if isinstance(cached, str):
        return ""
    return (
        f"off-the-shelf checkpoint {model_id} is not in the local Hugging Face cache and "
        "this machine has no hub access; run this comparison where the download is allowed"
    )


def _scores_to_rows(result: Any, labels: Sequence[str]) -> list[float]:
    return [float(result.scores.get(label, 0.0)) for label in labels]


def run_baseline(
    name: str,
    examples: Sequence[EmotionExample],
    *,
    labels: Sequence[str] = EMOTIONS,
    analyzer: Any | None = None,
    model_id: str | None = None,
) -> BaselineResult:
    """Score ``examples`` with one baseline. ``analyzer`` injects a double for tests."""

    labels = tuple(labels)
    _backend_on_path()

    if analyzer is None:
        if name == "keyword":
            from app.services.nlp.keyword import KeywordFallbackAnalyzer

            analyzer = KeywordFallbackAnalyzer()
            note = "shipped lexicon chain (Day 6); no tuned thresholds, operating point 0.5"
        elif name == "sentiment":
            from app.services.nlp.sentiment import SentimentAnalyzer

            analyzer = SentimentAnalyzer()
            note = "polarity scorer; can only band valence, so most labels stay at zero"
        elif name == "baseline":
            from app.services.nlp.hf import HFEmotionAnalyzer

            from manovia_ml.training.emotion.model import read_manifest  # noqa: F401

            resolved_id = model_id or BASELINE_MODEL_ID
            analyzer = HFEmotionAnalyzer(resolved_id)
            note = (
                f"off-the-shelf checkpoint {resolved_id} via the product's own HFEmotionAnalyzer "
                "and LABEL_MAP; needs a download, so it is only measurable on a machine with "
                "Hugging Face access"
            )
        else:
            raise ValueError(f"unknown baseline {name!r}; expected keyword, sentiment or baseline")
    else:
        note = f"{name} (injected analyzer)"

    probabilities = [
        _scores_to_rows(analyzer.analyze(example.text, example.lang), labels)
        for example in examples
    ]
    return BaselineResult(name=name, labels=labels, probabilities=probabilities, notes=note)
