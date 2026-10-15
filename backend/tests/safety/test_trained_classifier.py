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

import ast
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
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
#: ``ml/tests/test_safety_conformal.py::ABSTAIN_CASES`` — and the test at the
#: bottom of this file parses that file to prove the two still agree.
def _literal_frozenset(node: ast.expr) -> frozenset[str]:
    """Evaluate a literal set, written either as ``{...}`` or ``frozenset({...})``.

    ``ast.literal_eval`` refuses the call form, and the ML vectors use it because
    the policy's signature takes a frozenset — so the reader of *that* file sees
    the type the production code receives.
    """

    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id
        in {
            "frozenset",
            "set",
        }
    ):
        assert len(node.args) == 1, "frozenset() in a vector takes one argument"
        node = node.args[0]
    return frozenset(ast.literal_eval(node))


def _ml_abstain_cases() -> tuple[tuple[frozenset[str], str, str], ...]:
    """The abstention vectors, read from the ML suite that owns them.

    One copy of the vectors, in ``ml/tests/test_safety_conformal.py``, parsed
    with ``ast`` — not imported, because the backend must run without the ML
    extras and ``ml/`` must run without the backend. Parsing rather than
    duplicating is the point: a hand-kept second copy is a second thing to
    forget, and a forgotten mirror is worse than no mirror.

    A missing file is a hard error rather than a skip. A mirror that cannot be
    checked must not pretend to be checked.
    """

    ml_file = Path(__file__).resolve().parents[3] / "ml" / "tests" / "test_safety_conformal.py"
    if not ml_file.is_file():
        raise FileNotFoundError(
            f"the ML-side abstention vectors are missing: {ml_file}. They are the single "
            "source of truth for the MEDIUM-floor policy, and this suite refuses to run "
            "without them rather than fall back to a private copy."
        )
    tree = ast.parse(ml_file.read_text(encoding="utf-8"), filename=str(ml_file))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "ABSTAIN_CASES":
            continue
        if not isinstance(node.value, ast.Tuple):
            raise AssertionError("ABSTAIN_CASES must stay a tuple literal")
        out: list[tuple[frozenset[str], str, str]] = []
        for element in node.value.elts:
            assert isinstance(element, ast.Tuple) and len(element.elts) == 3, (
                "each ABSTAIN_CASES entry must be a 3-tuple (set, point, expected)"
            )
            out.append(
                (
                    _literal_frozenset(element.elts[0]),
                    ast.literal_eval(element.elts[1]),
                    ast.literal_eval(element.elts[2]),
                )
            )
        assert len(out) >= 10, f"expected the full vector set, found {len(out)}"
        return tuple(out)
    raise AssertionError(f"ABSTAIN_CASES not found in {ml_file}")


#: ``(set, point) -> expected abstention level``. Parsed from the ML suite, so
#: there is exactly one copy of these vectors in the repository.
ABSTAIN_CASES: tuple[tuple[frozenset[str], str, str], ...] = _ml_abstain_cases()

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


def test_a_torch_that_is_not_installed_is_a_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No torch means no model, remembered as a type — never a crash.

    ``sys.modules["torch"] = None`` makes ``import torch`` raise
    ``ModuleNotFoundError`` (an ``ImportError``), which the bridge turns into a
    remembered reason and the Day-9 baseline fallback.
    """

    monkeypatch.setitem(sys.modules, "torch", None)
    directory = _write_artifact(tmp_path)
    # No injected loader: the default one is what imports torch, so this is the
    # path a deployment without the training extra actually takes.
    classifier = TrainedSafetyClassifier(directory)

    with pytest.raises(ModelUnavailableError):
        classifier.predict("anything")

    assert classifier.failure.startswith("load_failed:")
    assert "ModuleNotFoundError" in classifier.failure


@pytest.mark.parametrize(("prediction", "point", "expected"), ABSTAIN_CASES)
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


class _FakeTensor:
    """A list of lists that knows the tensor operations the bridge uses."""

    def __init__(self, data: Any) -> None:
        self._data = data

    def __len__(self) -> int:
        return len(self._data)

    def __truediv__(self, temperature: float) -> _FakeTensor:
        rows = self._data
        if rows and isinstance(rows[0], list):
            return _FakeTensor([[value / temperature for value in row] for row in rows])
        return _FakeTensor([value / temperature for value in rows])

    def cpu(self) -> _FakeTensor:
        return self

    def to(self, _device: str) -> _FakeTensor:
        """The bridge moves every batch to the device; a CPU fake is already there."""

        return self

    def tolist(self) -> Any:
        return self._data


def _softmax(rows: list[list[float]]) -> list[list[float]]:
    """Row-wise softmax in plain Python: the fake must not need torch either."""

    out: list[list[float]] = []
    for row in rows:
        peak = max(row)
        exponentials = [pow(2.718281828459045, value - peak) for value in row]
        total = sum(exponentials)
        out.append([value / total for value in exponentials])
    return out


class _FakeTorch(ModuleType):
    """The torch surface ``_probabilities`` touches, and nothing else."""

    #: Some library resolves ``torch.Tensor`` by attribute at import time, so the
    #: fake has to answer to the name even though nothing here calls it.
    Tensor = _FakeTensor

    def __init__(self) -> None:
        super().__init__("torch")

    def tensor(self, data: Any) -> _FakeTensor:
        return _FakeTensor(data)

    def ones(self, *shape: int) -> _FakeTensor:
        if len(shape) == 2:
            return _FakeTensor([[1.0] * shape[1] for _ in range(shape[0])])
        return _FakeTensor([1.0] * shape[0])

    def no_grad(self) -> Any:
        class _Context:
            def __enter__(self) -> None:
                return None

            def __exit__(self, *_: object) -> None:
                return None

        return _Context()

    def softmax(self, logits: _FakeTensor, dim: int = -1) -> _FakeTensor:
        assert dim == -1, "the bridge only softmaxes the last dimension"
        return _FakeTensor(_softmax(logits._data))


@pytest.fixture(autouse=True)
def fake_torch(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Install a fake torch for every test in this module.

    ``autouse`` on purpose: a test that forgets the fixture would otherwise pass
    in a sandbox with torch installed and fail in CI, which is exactly the bug
    this replaces. One test below overrides it for the *missing* torch path.
    """

    monkeypatch.setitem(sys.modules, "torch", _FakeTorch())
    yield


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
        import torch  # the fake installed by the `fake_torch` fixture

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
        import torch  # the fake installed by the `fake_torch` fixture

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


# --------------------------------------------------------------------------- #
# The mirror: the same rule, implemented twice                                  #
# --------------------------------------------------------------------------- #


def test_the_backend_and_the_trainer_agree_on_every_abstention_vector() -> None:
    """The MEDIUM floor exists in two places, so it is checked in both.

    ``backend/.../trained.py::_abstain_level`` and
    ``ml/.../conformal.py::abstain_level`` are the same rule, written twice
    because ``ml/`` must run without the backend checkout and the backend must
    not import the ML extras. Two copies of a safety rule are one rule only while
    something checks they agree — so this test **parses the ML test file** rather
    than keeping a second hand-maintained copy of the vectors, and a reader who
    edits one side without the other fails here.

    Parsed with ``ast``: the vectors are a module-level tuple of 3-tuples, which
    is a shape a reader can check by eye and a parser can read without importing
    a package that is not installed.
    """

    for prediction, point, expected in ABSTAIN_CASES:
        assert _abstain_level(prediction, point=point) == expected, (
            f"{sorted(prediction)} at point {point!r}: backend says "
            f"{_abstain_level(prediction, point=point)!r}, the ML suite expects {expected!r}"
        )


def test_the_local_prediction_set_vectors_hold() -> None:
    """``SET_CASES`` stays a local copy: it is about this file's own reader.

    The abstention vectors are single-sourced from the ML suite; these are the
    *set* vectors, which is the one place the two implementations could diverge
    on the quantile arithmetic rather than on the policy.
    """

    for probabilities, quantile, expected in SET_CASES:
        assert _prediction_set(probabilities, quantile) == set(expected)


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


# Day-38 final-verification regressions: the repository's pre-existing safety
# coverage gate exposed untested selector and corrupt-metadata boundaries.
# Production code and the 95% gate remain unchanged.


@pytest.mark.parametrize("values", [{"max_length": 7}, {"batch_size": 0}])
def test_invalid_dimensions_fail_closed(tmp_path: Path, values: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        TrainedSafetyClassifier(
            tmp_path,
            max_length=values.get("max_length", 96),
            batch_size=values.get("batch_size", 8),
        )
    result = build_trained_safety_classifier(
        artifact_dir=str(tmp_path),
        max_length=values.get("max_length", 96),
        batch_size=values.get("batch_size", 8),
    )
    assert isinstance(result, NullClassifier)
    assert result.reason == "artifact_invalid"


@pytest.mark.parametrize(
    "manifest",
    [
        "{broken",
        "[]",
        '{"labels": []}',
        '{"labels": ["foreign"]}',
        '{"base_model": "local-fixture"}',
        "{}",
    ],
)
def test_corrupt_or_legacy_manifest_preserves_safe_defaults(tmp_path: Path, manifest: str) -> None:
    directory = _write_artifact(tmp_path)
    (directory / "manifest.json").write_text(manifest, encoding="utf-8")
    classifier = TrainedSafetyClassifier(directory, loader=_loader([[0.0] * 5]))
    assert classifier.predict("synthetic fixture") is not None
    assert classifier.labels == LEVEL_LABELS
    assert classifier.directory == directory
    assert classifier.max_length == 96
    assert classifier.is_loaded
    assert classifier.version == (
        "day33-local-fixture" if "local-fixture" in manifest else "day33-untagged"
    )


@pytest.mark.parametrize(
    "content",
    [
        "{broken",
        "[]",
        "{}",
        '{"decision_rule": "", "temperature": false, "conformal_quantile": "invalid"}',
        '{"decision_rule": 7, "temperature": -1, "conformal_quantile": 2}',
    ],
)
def test_corrupt_threshold_files_do_not_narrow_abstention(tmp_path: Path, content: str) -> None:
    directory = _write_artifact(tmp_path)
    for filename in ("calibration.json", "conformal.json", "thresholds.json"):
        (directory / filename).write_text(content, encoding="utf-8")
    classifier = TrainedSafetyClassifier(directory, loader=_loader([[0.0] * 5]))
    prediction = classifier.predict("synthetic fixture")
    assert classifier.temperature == 1
    assert classifier.quantile == 1
    assert prediction is not None
    assert set(prediction.prediction_set) == set(LEVEL_LABELS)


def test_abstention_can_be_disabled_without_hiding_point_prediction(tmp_path: Path) -> None:
    classifier = TrainedSafetyClassifier(
        _write_artifact(tmp_path), abstain=False, loader=_loader([[0.0, 1.0, 4.0, 0.0, 0.0]])
    )
    prediction = classifier.predict("synthetic fixture")
    assert prediction is not None
    assert prediction.level is RiskLevel.MEDIUM
    assert prediction.prediction_set == ()


def test_default_loader_reads_only_local_artifact_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fake the optional dependency, not the loading boundary under test."""
    from types import SimpleNamespace

    calls: list[str] = []

    class LocalModel(FakeModel):
        def to(self, device: str) -> LocalModel:
            assert device == "cpu"
            return self

    def tokenizer(path: str) -> FakeTokenizer:
        calls.append(path)
        return FakeTokenizer()

    def model(path: str, *, num_labels: int) -> LocalModel:
        calls.append(path)
        assert num_labels == 5
        return LocalModel([[0.0] * 5])

    module = ModuleType("transformers")
    module.AutoTokenizer = SimpleNamespace(from_pretrained=tokenizer)  # type: ignore[attr-defined]
    module.AutoModelForSequenceClassification = SimpleNamespace(from_pretrained=model)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", module)
    directory = _write_artifact(tmp_path)
    classifier = TrainedSafetyClassifier(directory)
    assert classifier.predict("synthetic fixture") is not None
    assert calls == [str(directory / "tokenizer"), str(directory / "model")]
    assert classifier.load_count == 1


def test_trained_selector_accepts_an_enabled_interface_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.safety import trained

    class EnabledFake(NullClassifier):
        enabled = True

    fake = EnabledFake()
    monkeypatch.setattr(trained, "build_trained_safety_classifier", lambda **_kwargs: fake)
    assert build_safety_classifier(_Settings(safety_ml="trained")) is fake


@pytest.mark.parametrize(
    "registry,missing_baseline,fails",
    [
        ("", False, False),
        ("", True, False),
        ("fixture", False, True),
        ("fixture", True, True),
        ("fixture", False, False),
    ],
)
def test_onnx_selector_records_fallback_or_returns_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    missing_baseline: bool,
    fails: bool,
) -> None:
    from app.services.ml_runtime import classifier as runtime

    fake = NullClassifier("fixture-provider")

    def provider(**kwargs: Any) -> NullClassifier:
        assert kwargs["registry_dir"] == registry
        if fails:
            raise RuntimeError("synthetic load failure")
        return fake

    monkeypatch.setattr(runtime, "build_onnx_safety_classifier", provider)
    result = build_safety_classifier(
        _Settings(
            safety_ml="onnx",
            ml_runtime_registry_dir=registry,
            safety_ml_artifact_dir=str(tmp_path / "absent") if missing_baseline else "",
        )
    )
    if registry and not fails:
        assert result is fake
    elif missing_baseline:
        assert isinstance(result, NullClassifier)
        assert result.reason == "artifact_missing"
    else:
        assert isinstance(result, TfidfLogisticClassifier)
        assert result.version.endswith("+onnx-unavailable")
