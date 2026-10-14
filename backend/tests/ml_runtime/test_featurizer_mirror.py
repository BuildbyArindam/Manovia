"""The backend featurizer must match the ML spec, index for index.

Golden values below are the same numbers ``ml/tests/test_compression_distill.py``
pins against ``manovia_ml.compression.featurizer``. If either implementation
changes the hash, tokeniser or normalisation, one of the two suites fails —
which is the point: the served model must see the features it was trained on.
"""

from __future__ import annotations

import math

from app.services.ml_runtime.featurizer import HashingFeaturizer


def test_golden_vector_matches_ml_spec() -> None:
    featurizer = HashingFeaturizer(dim=64, word_ngram_max=2, char_ngram_min=3, char_ngram_max=4)
    vector = featurizer.transform_one("I feel hopeless and tired")
    top = max(range(len(vector)), key=lambda i: vector[i])
    assert top == 11
    assert math.isclose(vector[11], 0.32359, abs_tol=1e-4)
    assert sum(1 for value in vector if value != 0.0) == 35


def test_from_spec_round_trips_and_checks_version() -> None:
    spec = {
        "version": 1,
        "dim": 32,
        "word_ngram_max": 1,
        "char_ngram_min": 3,
        "char_ngram_max": 3,
        "max_length_chars": 100,
    }
    featurizer = HashingFeaturizer.from_spec(spec)
    assert featurizer.dim == 32
    try:
        HashingFeaturizer.from_spec({**spec, "version": 99})
    except ValueError as exc:
        assert "version" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown spec version must be refused")


def test_empty_text_is_zero_vector() -> None:
    featurizer = HashingFeaturizer(dim=16)
    assert featurizer.transform_one("") == [0.0] * 16
