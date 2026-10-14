"""Hashed n-gram featurizer: the linear student's input, specified once.

The same featurizer runs in three places — distillation training here, the
frozen-eval scorer, and the backend's ONNX runtime
(``backend/app/services/ml_runtime/featurizer.py``). The backend cannot
import ``ml/`` (the two install separately), so the rule is restated there
and the two copies are pinned to each other by golden vectors: the backend
test hard-codes the same expected indices this module's test hard-codes,
so changing the hash, the tokeniser or the normalisation on either side
fails a suite instead of silently changing what the served model sees.

Specification (any change is a model change, not a refactor):

* NFC-normalise, casefold, truncate to ``max_length_chars``;
* word tokens are maximal ``[^\\W_]+`` runs (Unicode letters/digits), with
  unigrams and (optionally) bigrams;
* character n-grams span each word padded with spaces, lengths
  ``char_ngram_min..char_ngram_max``;
* each feature hashes with sha256 (never ``hash()``: process-randomised)
  to ``index = int(digest[:8]) % dim`` and adds 1.0;
* the vector is ``log1p``-scaled and L2-normalised (zero stays zero).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence

import numpy as np

__all__ = ["FEATURE_SPEC_VERSION", "HashingFeaturizer", "featurize_texts"]

#: Bump when the specification above changes; recorded in every manifest.
FEATURE_SPEC_VERSION = 1

_WORD_RE = re.compile(r"[^\W_]+")


def _feature_index(feature: str, dim: int) -> int:
    digest = hashlib.sha256(feature.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little") % dim


class HashingFeaturizer:
    """Deterministic text → fixed-width float32 vector (see module docstring)."""

    def __init__(
        self,
        *,
        dim: int = 1024,
        word_ngram_max: int = 2,
        char_ngram_min: int = 3,
        char_ngram_max: int = 5,
        max_length_chars: int = 2000,
    ) -> None:
        if dim < 1:
            raise ValueError("dim must be positive")
        if not 1 <= word_ngram_max <= 3:
            raise ValueError("word_ngram_max must be in [1, 3]")
        if char_ngram_min < 1 or char_ngram_max < char_ngram_min:
            raise ValueError("char n-gram range is invalid")
        self.dim = dim
        self.word_ngram_max = word_ngram_max
        self.char_ngram_min = char_ngram_min
        self.char_ngram_max = char_ngram_max
        self.max_length_chars = max_length_chars

    def spec(self) -> dict[str, int]:
        """The manifest block: everything a loader needs to rebuild this."""

        return {
            "version": FEATURE_SPEC_VERSION,
            "dim": self.dim,
            "word_ngram_max": self.word_ngram_max,
            "char_ngram_min": self.char_ngram_min,
            "char_ngram_max": self.char_ngram_max,
            "max_length_chars": self.max_length_chars,
        }

    def features(self, text: str) -> list[str]:
        """The feature strings for one text, in emission order (pre-hash)."""

        normalised = unicodedata.normalize("NFC", text)[: self.max_length_chars].casefold()
        words = _WORD_RE.findall(normalised)
        out: list[str] = []
        for n in range(1, self.word_ngram_max + 1):
            for i in range(len(words) - n + 1):
                out.append("w:" + " ".join(words[i : i + n]))
        for word in words:
            padded = f" {word} "
            for n in range(self.char_ngram_min, self.char_ngram_max + 1):
                for i in range(len(padded) - n + 1):
                    out.append("c:" + padded[i : i + n])
        return out

    def transform_one(self, text: str) -> np.ndarray:
        vector: np.ndarray = np.zeros(self.dim, dtype=np.float64)
        for feature in self.features(text):
            vector[_feature_index(feature, self.dim)] += 1.0
        vector = np.log1p(vector)
        norm = float(np.linalg.norm(vector))
        if norm > 0.0:
            vector /= norm
        result: np.ndarray = vector.astype(np.float32)
        return result

    def transform(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            empty: np.ndarray = np.zeros((0, self.dim), dtype=np.float32)
            return empty
        stacked: np.ndarray = np.stack([self.transform_one(text) for text in texts])
        return stacked


def featurize_texts(texts: Sequence[str], **kwargs: int) -> np.ndarray:
    """Convenience: build a featurizer from keyword spec and transform."""

    featurizer = HashingFeaturizer(
        dim=int(kwargs.get("dim", 1024)),
        word_ngram_max=int(kwargs.get("word_ngram_max", 2)),
        char_ngram_min=int(kwargs.get("char_ngram_min", 3)),
        char_ngram_max=int(kwargs.get("char_ngram_max", 5)),
        max_length_chars=int(kwargs.get("max_length_chars", 2000)),
    )
    return featurizer.transform(texts)
