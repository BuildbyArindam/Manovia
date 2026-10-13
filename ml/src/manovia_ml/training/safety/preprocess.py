"""The classifier's one normalisation step, shared by every model kind.

Unicode NFC, casefold, and a character window — the same three the backend's
serving path applies (``app.services.safety.ml_classifier.preprocess_text``), so
a model trained here and a model served there see the same bytes. The backend
does far more normalisation for its rules engine (leet, spelling, collapse); the
ML side deliberately does not, because a classifier that shares the engine's
normalisation is an echo of the engine rather than a second opinion.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["MAX_CLASSIFIER_CHARS", "preprocess_batch", "preprocess_text"]

#: Same window the rules engine reads: a megabyte paste must not reach a matcher.
MAX_CLASSIFIER_CHARS = 4000


def preprocess_text(text: str) -> str:
    """NFC, casefold, and the shared character window."""
    import unicodedata

    return unicodedata.normalize("NFC", text)[:MAX_CLASSIFIER_CHARS].casefold()


def preprocess_batch(texts: Sequence[str]) -> list[str]:
    """Vectorised form, for scikit-learn pipeline steps."""
    return [preprocess_text(item) for item in texts]
