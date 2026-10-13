"""Inference helpers shared by the trainer and the evaluator.

Kept in their own module so that ``evaluate.py`` can score an artefact without
importing ``train.py`` — the Day-32 rule that a number may only come from the
saved artefact, never from the process that produced it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from manovia_ml.training.safety.data import SafetyExample
from manovia_ml.training.safety.taxonomy import LEVELS

__all__ = ["predict_logits", "predict_probabilities", "resolve_device"]


def resolve_device(torch: Any, *, requested: str = "auto") -> str:
    """``auto`` -> ``cuda`` when there is one, else ``cpu``. Never raises."""
    if requested not in ("", "auto"):
        return requested
    try:
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # pragma: no cover - no torch build without cuda support
        return "cpu"


def predict_logits(
    model: Any,
    tokenizer: Any,
    examples: Sequence[SafetyExample],
    *,
    max_length: int,
    batch_size: int,
    device: str,
) -> list[list[float]]:
    """One forward pass per batch; returns raw logits in ``LEVELS`` order."""
    import torch

    out: list[list[float]] = []
    was_training = model.training
    model.eval()
    try:
        with torch.no_grad():
            for start in range(0, len(examples), max(1, batch_size)):
                batch = examples[start : start + max(1, batch_size)]
                encoded = tokenizer(
                    [example.text for example in batch],
                    return_tensors="pt",
                    truncation=True,
                    max_length=max_length,
                    padding=True,
                )
                encoded = {key: value.to(device) for key, value in encoded.items()}
                logits = model(**encoded).logits
                out.extend([[float(value) for value in row] for row in logits.cpu().tolist()])
    finally:
        if was_training:  # pragma: no cover - the trainer re-enables it anyway
            model.train()
    return out


def predict_probabilities(
    model: Any,
    tokenizer: Any,
    examples: Sequence[SafetyExample],
    *,
    max_length: int,
    batch_size: int,
    device: str,
    temperature: float = 1.0,
) -> list[dict[str, float]]:
    """Temperature-scaled softmax probabilities, as label -> probability maps."""
    from manovia_ml.training.safety.calibration import apply_temperature

    logits = predict_logits(
        model,
        tokenizer,
        examples,
        max_length=max_length,
        batch_size=batch_size,
        device=device,
    )
    return [dict(zip(LEVELS, apply_temperature(row, temperature), strict=True)) for row in logits]
