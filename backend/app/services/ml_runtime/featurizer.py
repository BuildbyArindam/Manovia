"""Hashed n-gram featurizer — the backend mirror of the ML spec.

This restates ``ml/src/manovia_ml/compression/featurizer.py`` because the
backend installs without ``ml/``. The two copies are pinned to each other
by golden vectors: ``tests/ml_runtime/test_featurizer_mirror.py``
hard-codes the same expected indices the ML suite hard-codes, so changing
the hash, tokeniser or normalisation on either side fails a suite instead
of silently changing what the served model sees.

Pure Python (lists, no numpy): this module must import in a deployment
without the serving extra, where it is simply never called.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Sequence

__all__ = ["FEATURE_SPEC_VERSION", "HashingFeaturizer"]

FEATURE_SPEC_VERSION = 1

_WORD_RE = re.compile(r"[^\W_]+")


def _feature_index(feature: str, dim: int) -> int:
    digest = hashlib.sha256(feature.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little") % dim


class HashingFeaturizer:
    """Deterministic text → fixed-width float vector (ML spec, mirrored)."""

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

    @classmethod
    def from_spec(cls, spec: dict[str, int]) -> HashingFeaturizer:
        if int(spec.get("version", -1)) != FEATURE_SPEC_VERSION:
            raise ValueError(f"unsupported featurizer spec version: {spec.get('version')!r}")
        return cls(
            dim=int(spec["dim"]),
            word_ngram_max=int(spec["word_ngram_max"]),
            char_ngram_min=int(spec["char_ngram_min"]),
            char_ngram_max=int(spec["char_ngram_max"]),
            max_length_chars=int(spec["max_length_chars"]),
        )

    def features(self, text: str) -> list[str]:
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

    def transform_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for feature in self.features(text):
            vector[_feature_index(feature, self.dim)] += 1.0
        vector = [math.log1p(value) for value in vector]
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0.0:
            vector = [value / norm for value in vector]
        return vector

    def transform(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.transform_one(text) for text in texts]
