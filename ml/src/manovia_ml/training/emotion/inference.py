"""Loading a packaged emotion artefact and turning text into labels.

This is the *runtime* half of Day 32: no training loop, no Optuna, no datasets —
load a directory, tokenize, run the sigmoids, apply the tuned thresholds. The
backend's ``TrainedEmotionAnalyzer`` mirrors this file's logic for the product
path; the duplication is deliberate (the product must not import `ml/`), and the
comparison test in ``backend/tests`` pins the two to the same numbers.

The artefact directory is the interface::

    model/            merged weights + config (no PEFT adapter to load)
    tokenizer/
    thresholds.json   per-label decision thresholds + the split they were tuned on
    manifest.json     base model, labels, library versions, data hashes, run id
    MODEL_CARD.md     the card that has to exist before a number is quoted

``thresholds.json`` missing is not an error: the model still runs at 0.5, and
:func:`load_artifact` says so in ``warnings`` — a missing artefact file that
silently changes every prediction is worse than one that says it is missing.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from manovia_ml.training.emotion.model import (
    THRESHOLDS_FILENAME,
    load_merged_model,
    read_manifest,
)
from manovia_ml.training.emotion.taxonomy import EMOTIONS, NEUTRAL

__all__ = [
    "EmotionArtifact",
    "load_artifact",
    "predict_probabilities",
    "predict_sets",
]

DEFAULT_THRESHOLD = 0.5


@dataclass
class EmotionArtifact:
    """A packaged model plus everything needed to interpret its output."""

    directory: Path
    model: Any
    tokenizer: Any
    labels: tuple[str, ...]
    thresholds: dict[str, float]
    manifest: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    device: str = "cpu"
    max_length: int = 128

    @property
    def base_model(self) -> str | None:
        return self.manifest.get("base_model")

    def threshold_for(self, label: str) -> float:
        return float(self.thresholds.get(label, DEFAULT_THRESHOLD))

    def predict_probabilities(
        self,
        texts: Sequence[str],
        *,
        batch_size: int = 16,
    ) -> list[list[float]]:
        return predict_probabilities(
            self.model, self.tokenizer, texts, max_length=self.max_length, batch_size=batch_size
        )

    def predict_sets(
        self,
        texts: Sequence[str],
        *,
        batch_size: int = 16,
    ) -> list[frozenset[str]]:
        probabilities = self.predict_probabilities(texts, batch_size=batch_size)
        return [
            frozenset(
                self.labels[index]
                for index, value in enumerate(row)
                if value >= self.threshold_for(self.labels[index])
            )
            for row in probabilities
        ]


def predict_probabilities(
    model: Any,
    tokenizer: Any,
    texts: Sequence[str],
    *,
    max_length: int = 128,
    batch_size: int = 16,
    device: str | None = None,
) -> list[list[float]]:
    """Sigmoid probabilities per label, one row per text, in model label order."""

    import torch  # imported here: the rest of this module is framework-free

    resolved_device = device or getattr(model, "device", "cpu")
    outputs: list[list[float]] = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start : start + batch_size])
            if not batch:
                continue
            encoded = tokenizer(
                batch,
                return_tensors="pt",
                truncation=True,
                max_length=max_length,
                padding=True,
            )
            encoded = {key: value.to(resolved_device) for key, value in encoded.items()}
            logits = model(**encoded).logits
            probabilities = torch.sigmoid(logits).cpu().tolist()
            outputs.extend(probabilities)
    return outputs


def predict_sets(
    probabilities: Sequence[Sequence[float]],
    labels: Sequence[str],
    thresholds: dict[str, float] | None = None,
) -> list[frozenset[str]]:
    """Apply per-label thresholds to a probability matrix."""

    resolved = thresholds or {}
    return [
        frozenset(
            label
            for index, label in enumerate(labels)
            if float(row[index]) >= float(resolved.get(label, DEFAULT_THRESHOLD))
        )
        for row in probabilities
    ]


def load_artifact(
    directory: str | Path,
    *,
    device: str = "cpu",
    max_length: int = 128,
    model_factory: Any | None = None,
    tokenizer_factory: Any | None = None,
) -> EmotionArtifact:
    """Load a packaged artefact, degrading with explicit warnings.

    Raises :class:`FileNotFoundError` when there is no model directory at all —
    that is the case the backend turns into "fall back to the lexicon chain".
    """

    root = Path(directory)
    model_dir = root / "model"
    tokenizer_dir = root / "tokenizer"
    if not model_dir.is_dir() or not tokenizer_dir.is_dir():
        raise FileNotFoundError(
            f"{root}: not a packaged emotion artefact (expected model/ and tokenizer/ "
            "directories; see ml/src/manovia_ml/training/emotion/model.py)"
        )

    warnings: list[str] = []
    thresholds_path = root / THRESHOLDS_FILENAME
    thresholds: dict[str, float] = {}
    if thresholds_path.is_file():
        payload = json.loads(thresholds_path.read_text(encoding="utf-8"))
        raw = payload.get("thresholds", payload) if isinstance(payload, dict) else {}
        thresholds = {str(label): float(value) for label, value in raw.items()}
    else:
        warnings.append(f"{thresholds_path} missing: every label falls back to {DEFAULT_THRESHOLD}")

    manifest = read_manifest(root)
    labels = tuple(manifest.get("labels", EMOTIONS))
    missing = [label for label in labels if label not in thresholds]
    if missing:
        warnings.append(f"no tuned threshold for {missing}: using {DEFAULT_THRESHOLD}")

    if tokenizer_factory is not None:
        tokenizer = tokenizer_factory(str(tokenizer_dir))
    else:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_dir))

    if model_factory is not None:
        model = model_factory(str(model_dir))
    else:
        model = load_merged_model(model_dir, device=device)
    model.eval()

    return EmotionArtifact(
        directory=root,
        model=model,
        tokenizer=tokenizer,
        labels=labels,
        thresholds=thresholds,
        manifest=manifest,
        warnings=warnings,
        device=device,
        max_length=max_length,
    )


def primary_label(row: Sequence[float], labels: Sequence[str]) -> str:
    """The argmax label — what the product UI calls the primary emotion.

    Ties break toward the earlier label in taxonomy order, and an all-zero row
    answers ``neutral``: "nothing to say" is a result, not a missing value.
    """

    best_index = max(range(len(labels)), key=lambda index: (float(row[index]), -index))
    if float(row[best_index]) <= 0.0:
        return NEUTRAL
    return labels[best_index]
