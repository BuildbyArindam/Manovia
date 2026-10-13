"""Load a run and predict. Small on purpose.

``EmotionPredictor`` is not wired into the Manovia API. It exists so that
(a) the smoke run can prove a logged model is loadable and usable later,
(b) a human can check what a model actually says before quoting its F1, and
(c) the format question ("what does a deployable artefact look like?") is settled
before anyone reaches for a serving framework.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from manovia_ml.serving.artifacts import Bundle, fetch_run_artifact, load_bundle

__all__ = ["EmotionPredictor", "Prediction", "main"]


@dataclass(frozen=True)
class Prediction:
    text: str
    label: str
    confidence: float | None


class EmotionPredictor:
    """A loaded bundle plus the little bit of behaviour callers want."""

    def __init__(self, bundle: Bundle) -> None:
        self._bundle = bundle

    @classmethod
    def from_path(
        cls, path: Path | str, *, allow_version_mismatch: bool = False
    ) -> EmotionPredictor:
        return cls(load_bundle(path, allow_version_mismatch=allow_version_mismatch))

    @classmethod
    def from_run(
        cls,
        run_id: str,
        *,
        tracking_uri: str | None = None,
        allow_version_mismatch: bool = False,
    ) -> EmotionPredictor:
        path = fetch_run_artifact(run_id, tracking_uri=tracking_uri)
        return cls(load_bundle(path, allow_version_mismatch=allow_version_mismatch))

    @property
    def labels(self) -> list[str]:
        return list(self._bundle.labels)

    @property
    def run_id(self) -> str | None:
        return self._bundle.run_id

    def predict(self, texts: Sequence[str]) -> list[Prediction]:
        items = [str(text) for text in texts]
        labels = self._bundle.predict(items)
        try:
            probabilities = self._bundle.predict_proba(items)
        except Exception:
            probabilities = []
        return [
            Prediction(
                text=text,
                label=label,
                confidence=(max(probabilities[index].values()) if probabilities else None),
            )
            for index, (text, label) in enumerate(zip(items, labels, strict=True))
        ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.serving.predictor",
        description="Predict with a logged run (manual check; not the API).",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run", help="MLflow run id")
    source.add_argument("--bundle", help="path to a model.joblib")
    parser.add_argument("--tracking-uri", default=None)
    parser.add_argument("texts", nargs="*", help="texts to score (default: read stdin)")
    args = parser.parse_args(argv)

    predictor = (
        EmotionPredictor.from_run(args.run, tracking_uri=args.tracking_uri)
        if args.run
        else EmotionPredictor.from_path(args.bundle)
    )

    texts = args.texts or [line.strip() for line in sys.stdin if line.strip()]
    if not texts:
        print("nothing to score", file=sys.stderr)
        return 2

    print(f"labels: {', '.join(predictor.labels)}")
    for prediction in predictor.predict(texts):
        confidence = f"{prediction.confidence:.3f}" if prediction.confidence is not None else "n/a"
        print(f"{prediction.label:>8} ({confidence})  {prediction.text}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
