"""The packaged artefact layout, and the mirror of it the backend relies on.

Two things are being pinned here. The first is the *layout contract*: the trainer
writes a directory, and ``backend/app/services/safety/trained.py`` reads one. The
backend cannot import the ML package (different dependency set, different
deployment), so it keeps its own copy of the file list. When one side adds a file
and the other does not, the failure is silent and only shows up as a model that
loads without its calibration — which is a model that over-claims confidence.

The second is the trainable-parameter report. A LoRA run that reports a trainable
count equal to the full model has not frozen anything, and a reviewer who sees
"100% trainable" on a 278M-parameter encoder should immediately suspect it.

Neither test needs a training run: the layout is a constant list and the parameter
report is arithmetic over a ``SafetyLoRABuild``.
"""

from __future__ import annotations

import pytest

from manovia_ml.training.safety.model import (
    CALIBRATION_FILENAME,
    CARD_FILENAME,
    CONFORMAL_FILENAME,
    MANIFEST_FILENAME,
    MODEL_DIRNAME,
    THRESHOLDS_FILENAME,
    TOKENIZER_DIRNAME,
    SafetyLoRABuild,
    trainable_parameter_report,
)

pytest.importorskip("torch", reason='install the `training` extra: pip install -e ".[training]"')
pytest.importorskip("transformers", reason="install the `training` extra")


def test_the_backend_and_the_trainer_agree_on_the_artefact_files() -> None:
    """The mirror must be exact, in both directions.

    The backend's list lives in ``app/services/safety/trained.py`` so it can be
    read without the ML extras installed. If either side drifts, the serving path
    either fails to load a file the trainer wrote, or loads a directory the
    trainer never produced — both of which surface as "the model is there but the
    calibration is missing", i.e. an over-confident model in production.

    Parsed with ``ast`` rather than grepped, so a renamed constant is caught
    instead of being spelled out twice in this file.
    """

    import ast
    from pathlib import Path

    backend = (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "app"
        / "services"
        / "safety"
        / "trained.py"
    )
    assert backend.is_file(), backend
    tree = ast.parse(backend.read_text(encoding="utf-8"), filename=str(backend))

    names: dict[str, str] = {}

    def collect(target: ast.expr, value: ast.expr) -> None:
        # Both spellings appear in that file: `X: Final = "..."` (an AnnAssign)
        # and a plain `X = "..."`. Collect both, or the tuple below cannot be
        # resolved and the check silently compares nothing.
        if (
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and isinstance(target, ast.Name)
        ):
            names[target.id] = value.value

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                collect(target, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            collect(node.target, node.value)

    backend_files: tuple[str, ...] = ()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "ARTIFACT_FILES" or node.value is None:
            continue
        assert isinstance(node.value, ast.Tuple), "ARTIFACT_FILES must be a literal tuple"
        backend_files = tuple(
            names[element.id] for element in node.value.elts if isinstance(element, ast.Name)
        )
    assert backend_files, "ARTIFACT_FILES was not found in the backend bridge"

    trainer_files = (
        MODEL_DIRNAME,
        TOKENIZER_DIRNAME,
        CALIBRATION_FILENAME,
        CONFORMAL_FILENAME,
        THRESHOLDS_FILENAME,
        MANIFEST_FILENAME,
        CARD_FILENAME,
    )
    assert set(backend_files) == set(trainer_files), (
        f"trainer-only: {sorted(set(trainer_files) - set(backend_files))}; "
        f"backend-only: {sorted(set(backend_files) - set(trainer_files))}"
    )


def test_the_artefact_filenames_are_stable_names_not_dates() -> None:
    """A version is a name, not a timestamp: the config points at a path."""

    assert MANIFEST_FILENAME == "manifest.json"
    assert CARD_FILENAME == "MODEL_CARD.md"
    for name in (CONFORMAL_FILENAME, THRESHOLDS_FILENAME):
        assert name.islower(), name


def test_the_trainable_parameter_report_names_the_two_numbers_a_reviewer_wants() -> None:
    """LoRA's whole claim, stated as arithmetic: a small trainable count.

    The report is what the run logs, so a reviewer comparing a LoRA run with a
    full fine-tune reads exactly these two keys. A LoRA run whose trainable count
    equals its total count has frozen nothing, and this is where that shows up.
    """

    class _Parameter:
        def __init__(self, numel: int, requires_grad: bool) -> None:
            self._numel = numel
            self.requires_grad = requires_grad

        def numel(self) -> int:
            return self._numel

    class _Model:
        def parameters(self) -> list[_Parameter]:
            return [
                _Parameter(1_000_000, False),  # frozen base
                _Parameter(4_000, True),  # adapter
                _Parameter(2_000, True),  # classifier head
            ]

    build = SafetyLoRABuild(
        model=_Model(),
        tokenizer=None,
        labels=("none", "low", "medium", "high", "imminent"),
        trainable_parameters=6_000,
        total_parameters=1_006_000,
    )

    report = trainable_parameter_report(build)

    assert report == {"parameters.total": 1_006_000, "parameters.trainable": 6_000}
    # The derived share is the property, not the report: one decimal-free ratio
    # a reviewer can check by eye.
    assert build.trainable_share == pytest.approx(6_000 / 1_006_000)
    assert build.trainable_share < 0.01, "a LoRA run with >1% trainable froze nothing"


def test_a_build_with_no_parameters_has_a_zero_share_not_a_crash() -> None:
    build = SafetyLoRABuild(
        model=None,
        tokenizer=None,
        labels=("none",),
        trainable_parameters=0,
        total_parameters=0,
    )

    assert build.trainable_share == 0.0
    assert trainable_parameter_report(build) == {
        "parameters.total": 0,
        "parameters.trainable": 0,
    }


def test_the_build_records_the_label_order_it_was_trained_with() -> None:
    """The serving path reads the label order out of the artefact.

    It has to be the taxonomy's order (none < low < medium < high < imminent),
    not whatever order the corpus happened to yield, because the abstention
    policy and the cost matrix both index by position.
    """

    build = SafetyLoRABuild(
        model=None,
        tokenizer=None,
        labels=("none", "low", "medium", "high", "imminent"),
        trainable_parameters=1,
        total_parameters=2,
    )

    assert build.labels == ("none", "low", "medium", "high", "imminent")
