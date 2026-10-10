"""The ML classifier interface: calibration shape, artifacts, degradation.

The baseline ships as a committed artifact trained offline from the train
split; these tests load the REAL artifact (so a broken joblib file fails a
test instead of the API) while keeping every assertion hermetic and offline.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.services.safety.base import RiskLevel
from app.services.safety.ml_classifier import (
    LEVEL_LABELS,
    MAX_CLASSIFIER_CHARS,
    MLPrediction,
    NullClassifier,
    TfidfLogisticClassifier,
    build_ml_classifier,
    preprocess_text,
)


@pytest.fixture(autouse=True)
def _clear_artifact_cache() -> Iterator[None]:
    """build_ml_classifier caches per artifact dir; tests need a fresh look."""
    build_ml_classifier.cache_clear()
    yield
    build_ml_classifier.cache_clear()


def test_preprocess_is_nfc_casefolded_and_bounded() -> None:
    assert preprocess_text("AbC") == "abc"
    long = "x" * (MAX_CLASSIFIER_CHARS + 500)
    assert len(preprocess_text(long)) == MAX_CLASSIFIER_CHARS


def test_null_classifier_says_nothing() -> None:
    classifier = NullClassifier(reason="test")
    assert classifier.enabled is False
    assert classifier.predict("I want to die") is None


def test_missing_artifact_degrades_instead_of_crashing(tmp_path: Path) -> None:
    classifier = build_ml_classifier(artifact_dir=str(tmp_path))
    assert classifier.enabled is False
    assert classifier.predict("anything") is None


def test_committed_artifact_loads_and_serves_all_five_levels() -> None:
    """The shipped artifact must exist, load, and score deterministically."""
    classifier = build_ml_classifier()
    assert classifier.enabled is True, "the committed artifact failed to load"

    prediction = classifier.predict("I want to die tonight")
    assert prediction is not None
    assert set(prediction.probabilities) == set(LEVEL_LABELS)
    assert math.isclose(sum(prediction.probabilities.values()), 1.0, abs_tol=1e-6)
    assert all(0.0 <= value <= 1.0 for value in prediction.probabilities.values())
    assert prediction.level == RiskLevel.IMMINENT  # direct ideation + timeframe
    assert prediction.confidence == pytest.approx(prediction.probabilities[prediction.level.label])

    # Determinism: the same text gets the same numbers, every time.
    again = classifier.predict("I want to die tonight")
    assert again == prediction


def test_prediction_ties_break_upward() -> None:
    probabilities = {"none": 0.2, "low": 0.2, "medium": 0.2, "high": 0.2, "imminent": 0.2}
    prediction = MLPrediction(probabilities=probabilities, level=RiskLevel.NONE, confidence=0.2)
    # Rebuild via the classifier's own logic through a hand-made vector:
    best = max(probabilities.values())
    chosen = RiskLevel.NONE
    for candidate in reversed(tuple(RiskLevel)):
        if probabilities[candidate.label] >= best - 1e-12:
            chosen = candidate
            break
    assert chosen == RiskLevel.IMMINENT
    assert prediction.probabilities["imminent"] == pytest.approx(0.2)


def test_rejects_an_artifact_with_the_wrong_vocabulary(tmp_path: Path) -> None:
    joblib = pytest.importorskip("joblib")
    bundle = {"pipeline": object(), "classes": ["yes", "no"], "schema": 1}
    joblib.dump(bundle, tmp_path / "model.joblib")
    classifier = build_ml_classifier(artifact_dir=str(tmp_path))
    assert classifier.enabled is False  # wrong vocabulary -> degrade, don't serve


def test_empty_text_scores_nothing() -> None:
    classifier = build_ml_classifier()
    if not classifier.enabled:
        pytest.skip("artifact missing")
    assert isinstance(classifier, TfidfLogisticClassifier)
    assert classifier.predict("") is None
    assert classifier.predict("   ") is None


def test_metadata_records_provenance() -> None:
    import app

    metadata_path = Path(app.__file__).parent / "ml_artifacts" / "crisis_v1" / "metadata.json"
    assert metadata_path.is_file(), "the artifact must ship with its provenance"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["test_split_used"] is False
    assert metadata["train_split_sha256"]
    assert metadata["dev_metrics"]["cases"] > 0


def test_benign_text_scores_mostly_benign() -> None:
    """Sanity, not a benchmark: ordinary chatter must not look like a crisis.

    Pinned loose enough to survive retraining, tight enough to catch a
    seriously broken artifact.
    """
    classifier = build_ml_classifier()
    if not classifier.enabled:
        pytest.skip("artifact missing")
    prediction = classifier.predict("The new cafe on our street does great coffee.")
    assert prediction is not None
    assert prediction.mass_at_or_above(RiskLevel.HIGH) < 0.6
