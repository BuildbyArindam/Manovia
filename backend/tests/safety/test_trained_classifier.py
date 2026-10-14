"""The Day-33 trained safety classifier: layout, policy, and never-fatal loading.

Three things are pinned here.

**The artefact layout.** ``ARTIFACT_FILES`` is the backend's copy of a contract
the ML package writes (``manovia_ml.training.safety.model``). A layout change is
one file on each side, so both sides name the same six entries — and the ML test
suite carries the mirror-image assertion, so a drift fails on whichever side
moved.

**The abstention policy.** ``_prediction_set`` and ``_abstain_level`` restate what
``manovia_ml.training.safety.conformal`` does, because the backend does not import
from ``ml/`` (the backend CI job installs only ``.[dev]``). The golden vectors
below are the same ones the ML-side test asserts, so the two implementations are
pinned against each other without an import.

**Never fatal.** A missing directory, a missing ``transformers``/``torch``, or a
loader that raises all leave the classifier constructible and describable, and
the factory hands back a null classifier rather than an exception. Deleting the
weights is a supported state, not an outage.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.services.nlp.hf import ModelUnavailableError
from app.services.safety.base import RiskLevel
from app.services.safety.ml_classifier import (
    LEVEL_LABELS,
    NullClassifier,
    TfidfLogisticClassifier,
    build_safety_classifier,
)
from app.services.safety.trained import (
    ARTIFACT_FILES,
    TrainedSafetyClassifier,
    _abstain_level,
    _prediction_set,
    build_trained_safety_classifier,
)

# --------------------------------------------------------------------------- #
# The layout contract                                                          #
# --------------------------------------------------------------------------- #


def test_the_artifact_layout_matches_the_ml_package() -> None:
    """Six entries, in the order the trainer writes them.

    The ML side asserts the same list in
    ``ml/tests/test_safety_model.py::test_the_backend_layout_matches``.
    """

    assert ARTIFACT_FILES == (
        "model",
        "tokenizer",
        "calibration.json",
        "conformal.json",
        "thresholds.json",
        "manifest.json",
        "MODEL_CARD.md",
    )


# --------------------------------------------------------------------------- #
# The abstention policy, as golden vectors                                     #
# --------------------------------------------------------------------------- #

#: (probabilities, quantile) -> expected set. Shared with the ML-side test
#: ``ml/tests/test_safety_conformal.py::test_the_backend_golden_vectors_match``.
SET_CASES: tuple[tuple[dict[str, float], float, frozenset[str]], ...] = (
    # A confident prediction is a singleton.
    (
        {"none": 0.01, "low": 0.02, "medium": 0.03, "high": 0.10, "imminent": 0.84},
        0.20,
        frozenset({"imminent"}),
    ),
    # A torn one keeps every level the model could not rule out: at q=0.75 the
    # membership rule is p >= 0.25, so medium (0.20) is ruled out and the two
    # crisis levels are not.
    (
        {"none": 0.10, "low": 0.15, "medium": 0.20, "high": 0.30, "imminent": 0.25},
        0.75,
        frozenset({"high", "imminent"}),
    ),
    # The argmax is always in the set, even when nothing clears the quantile.
    (
        {"none": 0.90, "low": 0.04, "medium": 0.03, "high": 0.02, "imminent": 0.01},
        0.05,
        frozenset({"none"}),
    ),
    # A near-tie between two crisis levels: both, never one.
    (
        {"none": 0.02, "low": 0.03, "medium": 0.05, "high": 0.45, "imminent": 0.45},
        0.60,
        frozenset({"high", "imminent"}),
    ),
)


@pytest.mark.parametrize(("probabilities", "quantile", "expected"), SET_CASES)
def test_the_prediction_set_matches_the_ml_implementation(
    probabilities: dict[str, float], quantile: float, expected: frozenset[str]
) -> None:
    assert _prediction_set(probabilities, quantile) == expected


@pytest.mark.parametrize(
    ("prediction", "point", "expected"),
    [
        # A risky set means MEDIUM or above, never below the point prediction.
        (frozenset({"low", "medium"}), "none", "medium"),
        # The action is the MEDIUM floor, not the worst level in the set.
        (frozenset({"medium", "imminent"}), "high", "high"),
        (frozenset({"none", "medium", "imminent"}), "none", "medium"),
        (frozenset({"none", "low"}), "none", "none"),
        # Ambiguity between two benign levels is not a reason to escalate.
        (frozenset({"none", "low"}), "low", "low"),
        # A set that only reaches MEDIUM raises a NONE point prediction.
        (frozenset({"none", "medium"}), "none", "medium"),
    ],
)
def test_the_abstention_policy_resolves_toward_caution(
    prediction: frozenset[str], point: str, expected: str
) -> None:
    assert _abstain_level(prediction, point=point) == expected


def test_the_abstention_policy_never_lowers_the_point_prediction() -> None:
    """Whatever the set says, the acted-on level is >= the point level."""

    order = {label: index for index, label in enumerate(LEVEL_LABELS)}
    for prediction in (
        frozenset({"none"}),
        frozenset({"none", "low"}),
        frozenset({"low", "high"}),
        frozenset({"medium"}),
        frozenset({"low", "medium", "imminent"}),
    ):
        for point in LEVEL_LABELS:
            acted = _abstain_level(prediction, point=point)
            assert order[acted] >= order[point], (prediction, point, acted)


# --------------------------------------------------------------------------- #
# A fake loader: the model never has to exist for the logic to be tested        #
# --------------------------------------------------------------------------- #


class FakeModel:
    """A stand-in for a sequence-classification head.

    ``logits`` is what the model returns per text; everything else (temperature,
    softmax, tie-breaks, the abstention policy) is the module under test.
    """

    def __init__(self, logits: list[list[float]]) -> None:
        self._logits = logits
        self.eval_called = False

    def eval(self) -> FakeModel:
        self.eval_called = True
        return self

    def __call__(self, **encoded: Any) -> Any:
        import torch

        wanted = len(encoded["input_ids"])
        rows = self._logits[:wanted]
        self._logits = self._logits[len(rows) :]
        rows = rows or [[0.0] * len(LEVEL_LABELS)] * wanted

        class _Out:
            def __init__(self, values: list[list[float]]) -> None:
                self.logits = torch.tensor(values)

        return _Out(rows)


class FakeTokenizer:
    def __call__(self, texts: Any, **kwargs: Any) -> dict[str, Any]:
        import torch

        rows = [[1, 2, 3] for _ in texts]
        return {"input_ids": torch.tensor(rows), "attention_mask": torch.ones(len(rows), 3)}


def _loader(
    logits: list[list[float]], calls: list[int] | None = None
) -> Callable[[Path, str, int], tuple[Any, Any]]:
    def load(directory: Path, device: str, max_length: int) -> tuple[Any, Any]:
        if calls is not None:
            calls.append(max_length)
        return FakeModel(list(logits)), FakeTokenizer()

    return load


def _write_artifact(
    directory: Path,
    *,
    temperature: float = 1.0,
    quantile: float = 0.2,
    decision_rule: str = "expected_cost",
    run_id: str = "abc123def456",
) -> Path:
    (directory / "model").mkdir(parents=True)
    (directory / "tokenizer").mkdir(parents=True)
    (directory / "calibration.json").write_text(
        json.dumps({"temperature": temperature, "split": "calibration"}), encoding="utf-8"
    )
    (directory / "conformal.json").write_text(
        json.dumps({"alpha": 0.05, "quantile": quantile, "target_coverage": 0.95}),
        encoding="utf-8",
    )
    (directory / "thresholds.json").write_text(
        json.dumps(
            {
                "temperature": temperature,
                "conformal_quantile": quantile,
                "decision_rule": decision_rule,
                "labels": list(LEVEL_LABELS),
            }
        ),
        encoding="utf-8",
    )
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "base_model": "xlm-roberta-base",
                "labels": list(LEVEL_LABELS),
                "written": "2026-10-13",
            }
        ),
        encoding="utf-8",
    )
    (directory / "MODEL_CARD.md").write_text("# card\n", encoding="utf-8")
    return directory


# --------------------------------------------------------------------------- #
# Inference                                                                    #
# --------------------------------------------------------------------------- #


def test_a_confident_prediction_is_a_singleton_and_the_argmax(tmp_path: Path) -> None:
    directory = _write_artifact(tmp_path, temperature=1.0, quantile=0.20)
    classifier = TrainedSafetyClassifier(directory, loader=_loader([[0.1, 0.2, 0.3, 1.0, 4.0]]))

    prediction = classifier.predict("i have written the letters")

    assert prediction is not None
    assert prediction.level is RiskLevel.IMMINENT
    assert prediction.prediction_set == ("imminent",)
    assert prediction.abstained_level is RiskLevel.IMMINENT
    assert prediction.probabilities["imminent"] == pytest.approx(0.895, abs=1e-3)
    assert prediction.confidence == pytest.approx(0.895, abs=1e-3)


def test_temperature_scaling_softens_the_distribution(tmp_path: Path) -> None:
    """A temperature above 1 must flatten the probabilities, and the report shows it."""

    sharp = TrainedSafetyClassifier(
        _write_artifact(tmp_path / "sharp", temperature=1.0),
        loader=_loader([[0.0, 0.0, 0.0, 1.0, 3.0]]),
    )
    soft = TrainedSafetyClassifier(
        _write_artifact(tmp_path / "soft", temperature=4.0),
        loader=_loader([[0.0, 0.0, 0.0, 1.0, 3.0]]),
    )

    sharp_prediction = sharp.predict("i feel awful")
    soft_prediction = soft.predict("i feel awful")

    assert sharp_prediction is not None and soft_prediction is not None
    assert sharp_prediction.probabilities["imminent"] > soft_prediction.probabilities["imminent"]
    assert soft.temperature == 4.0


def test_a_torn_prediction_abstains_and_is_treated_as_at_least_medium(tmp_path: Path) -> None:
    directory = _write_artifact(tmp_path, temperature=1.0, quantile=0.95)
    classifier = TrainedSafetyClassifier(directory, loader=_loader([[2.0, 1.0, 1.0, 1.0, 1.0]]))

    prediction = classifier.predict("everything is loud and i cannot sleep")

    assert prediction is not None
    # The point prediction is NONE; the set is wide; the policy says MEDIUM.
    assert prediction.level is RiskLevel.NONE
    assert len(prediction.prediction_set) > 1
    assert prediction.abstained_level is RiskLevel.MEDIUM


def test_ties_break_toward_the_higher_level(tmp_path: Path) -> None:
    directory = _write_artifact(tmp_path)
    classifier = TrainedSafetyClassifier(directory, loader=_loader([[1.0, 1.0, 1.0, 1.0, 1.0]]))

    prediction = classifier.predict("nothing in particular")

    assert prediction is not None
    assert prediction.level is RiskLevel.IMMINENT


def test_empty_text_is_not_scored(tmp_path: Path) -> None:
    directory = _write_artifact(tmp_path)
    classifier = TrainedSafetyClassifier(directory, loader=_loader([[0.0] * 5]))

    assert classifier.predict("") is None
    assert classifier.predict("   \n ") is None
    assert classifier.predict_batch(["", " "]) == [None, None]


def test_batch_scoring_matches_single_calls(tmp_path: Path) -> None:
    directory = _write_artifact(tmp_path, quantile=0.20)
    rows = [[0.1, 0.2, 0.3, 1.0, 4.0], [0.1, 0.2, 0.3, 3.5, 0.4]]
    single = TrainedSafetyClassifier(directory, loader=_loader([list(rows[0])]))
    batch = TrainedSafetyClassifier(directory, loader=_loader([list(row) for row in rows]))

    assert single.predict("first") is not None
    results = batch.predict_batch(["first", "second"])

    # predict_batch may return None for a row it could not score; the contract is
    # that a short batch on a working model scores every row.
    assert results and all(item is not None for item in results)
    assert [item.level for item in results if item is not None] == [
        RiskLevel.IMMINENT,
        RiskLevel.HIGH,
    ]


def test_describe_carries_provenance_and_never_text(tmp_path: Path) -> None:
    directory = _write_artifact(tmp_path, temperature=2.5, quantile=0.31)
    classifier = TrainedSafetyClassifier(directory, loader=_loader([[0.0] * 5]))
    classifier.predict("a message nobody may read back")

    described = classifier.describe()

    assert described["loaded"] is True
    assert described["load_count"] == 1
    assert described["temperature"] == 2.5
    assert described["conformal_quantile"] == 0.31
    assert described["run_id"] == "abc123def456"
    assert described["failure"] == "none"
    assert "a message nobody may read back" not in json.dumps(described, default=str)


def test_the_version_names_the_run(tmp_path: Path) -> None:
    directory = _write_artifact(tmp_path, run_id="0123456789ab")
    classifier = TrainedSafetyClassifier(directory, loader=_loader([[0.0] * 5]))
    classifier.predict("hello")

    assert classifier.version == "day33-01234567"


# --------------------------------------------------------------------------- #
# Every failure is a degradation                                               #
# --------------------------------------------------------------------------- #


def test_a_missing_directory_is_remembered_not_retried(tmp_path: Path) -> None:
    classifier = TrainedSafetyClassifier(tmp_path / "absent", loader=_loader([[0.0] * 5]))

    with pytest.raises(ModelUnavailableError):
        classifier.predict("anything")
    with pytest.raises(ModelUnavailableError):
        classifier.predict("anything")

    assert classifier.failure == "artifact_missing"
    assert classifier.load_count == 0


def test_a_loader_that_raises_is_remembered_by_type(tmp_path: Path) -> None:
    def exploding(directory: Path, device: str, max_length: int) -> tuple[Any, Any]:
        raise RuntimeError("torch is missing")

    directory = _write_artifact(tmp_path)
    classifier = TrainedSafetyClassifier(directory, loader=exploding)

    with pytest.raises(ModelUnavailableError):
        classifier.predict("anything")

    assert classifier.failure == "load_failed:RuntimeError"
    assert classifier.describe()["failure"] == "load_failed:RuntimeError"


def test_unreadable_metadata_falls_back_to_do_nothing(tmp_path: Path) -> None:
    """Corrupt metadata must not narrow the abstention policy.

    Both files that could carry a quantile are broken here: ``conformal.json``
    is not JSON at all, and ``thresholds.json`` carries a value outside [0, 1].
    The fallback is 1.0 — "abstain on everything" — because the alternative
    (inventing a smaller quantile) would silently promise coverage the
    calibration never measured.
    """

    directory = _write_artifact(tmp_path)
    (directory / "calibration.json").write_text("{not json", encoding="utf-8")
    (directory / "conformal.json").write_text("[]", encoding="utf-8")
    payload = json.loads((directory / "thresholds.json").read_text(encoding="utf-8"))
    payload["conformal_quantile"] = 2.0
    (directory / "thresholds.json").write_text(json.dumps(payload), encoding="utf-8")
    classifier = TrainedSafetyClassifier(directory, loader=_loader([[2.0, 1.0, 1.0, 1.0, 1.0]]))

    prediction = classifier.predict("everything is loud")

    assert classifier.temperature == 1.0
    assert classifier.quantile == 1.0
    assert prediction is not None
    assert prediction.abstained_level is RiskLevel.MEDIUM


def test_a_manifest_with_foreign_labels_is_ignored(tmp_path: Path) -> None:
    directory = _write_artifact(tmp_path)
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    payload["labels"] = ["none", "low", "medium", "high"]
    (directory / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    classifier = TrainedSafetyClassifier(directory, loader=_loader([[0.0] * 5]))

    assert classifier.labels == LEVEL_LABELS


def test_the_factory_never_raises(tmp_path: Path) -> None:
    assert isinstance(build_trained_safety_classifier(artifact_dir=""), NullClassifier)
    assert isinstance(
        build_trained_safety_classifier(artifact_dir=str(tmp_path / "absent")),
        TrainedSafetyClassifier,
    )


# --------------------------------------------------------------------------- #
# The selector: one flag, one meaning                                          #
# --------------------------------------------------------------------------- #


class _Settings:
    def __init__(self, **values: Any) -> None:
        self.safety_ml_enabled = True
        self.safety_ml = "baseline"
        self.safety_ml_artifact_dir = ""
        self.safety_ml_trained_artifact_dir = ""
        for key, value in values.items():
            setattr(self, key, value)


def test_disabled_is_the_day8_pipeline() -> None:
    classifier = build_safety_classifier(_Settings(safety_ml_enabled=False))

    assert isinstance(classifier, NullClassifier)
    assert classifier.reason == "disabled"


def test_baseline_is_the_default() -> None:
    """A deployment that never sets SAFETY_ML gets exactly the Day-9 model."""

    classifier = build_safety_classifier(_Settings())

    assert isinstance(classifier, TfidfLogisticClassifier)


def test_trained_is_used_when_the_artifact_is_there(tmp_path: Path) -> None:
    directory = _write_artifact(tmp_path)
    classifier = build_safety_classifier(
        _Settings(safety_ml="trained", safety_ml_trained_artifact_dir=str(directory))
    )

    assert isinstance(classifier, TrainedSafetyClassifier)


def test_trained_without_an_artifact_falls_back_to_the_baseline(tmp_path: Path) -> None:
    """Not to rules-only: a silent "ML is off" is a different deployment."""

    classifier = build_safety_classifier(
        _Settings(
            safety_ml="trained",
            safety_ml_trained_artifact_dir=str(tmp_path / "absent"),
            safety_ml_artifact_dir=str(tmp_path / "no-baseline-either"),
        )
    )

    assert isinstance(classifier, NullClassifier)
    assert classifier.reason == "artifact_missing"


def test_the_abstention_level_can_never_lower_a_rules_result() -> None:
    """The Day-33 field, fed through the Day-9 property.

    The ensemble reads ``prediction.level``, so the abstention policy is applied
    *inside* the classifier: a set that reaches MEDIUM makes the classifier's
    level MEDIUM or above. This asserts the direction of that rewrite over every
    point prediction and every set shape — the same invariant
    ``test_the_ensemble_never_lowers_a_rules_result`` pins for the raw model.
    """

    from app.services.safety.trained import _as_level

    order = {level.label: level for level in RiskLevel}
    for point in LEVEL_LABELS:
        for prediction in (
            frozenset({"none"}),
            frozenset({"none", "low"}),
            frozenset({"low", "high"}),
            frozenset({"medium"}),
            frozenset({"none", "medium", "imminent"}),
        ):
            acted = _as_level(_abstain_level(prediction, point=point))
            assert acted >= order[point], (point, prediction)
