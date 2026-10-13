"""The `dvc.lock` consistency check: missing outputs are fine, moved deps are not.

The distinction these tests pin is the whole reason the check exists. A fresh
clone has no splits on disk (`changed outs: deleted`) and that must pass; a lock
file that names a different version of the code than the one in the repository
(`changed deps`) must fail, because then the recorded data was not produced by
the pipeline the commit contains.

The parsing is tested here as a pure function; the subprocess half runs in CI
(`ml-smoke`, before `dvc repro`) and was exercised by hand in both states.
"""

from __future__ import annotations

from manovia_ml.data.dvc_lock import changed_deps

FRESH_CLONE = {
    "toy_split": [
        {
            "changed outs": {
                "data/processed/toy_train.jsonl": "deleted",
                "data/processed/toy.manifest.json": "deleted",
            }
        }
    ],
    "toy_smoke_split": [{"changed outs": {"data/processed/toy_smoke_train.jsonl": "deleted"}}],
}

STALE_LOCK = {
    "toy_split": [
        {"changed deps": {"src/manovia_ml/data/toy.py": "modified"}},
        {"changed outs": {"data/processed/toy_train.jsonl": "deleted"}},
    ],
    "toy_smoke_split": [{"changed deps": {"src/manovia_ml/data/toy.py": "modified"}}],
}


def test_missing_outputs_are_not_a_failure() -> None:
    """A clone that has not built the splits yet is the normal state of CI."""

    assert changed_deps(FRESH_CLONE) == {}


def test_an_up_to_date_pipeline_is_empty() -> None:
    assert changed_deps({"toy_split": [{"changed outs": {}}]}) == {}
    assert changed_deps({}) == {}


def test_a_moved_dependency_is_reported_per_stage() -> None:
    moved = changed_deps(STALE_LOCK)

    assert set(moved) == {"toy_split", "toy_smoke_split"}
    assert moved["toy_split"]["src/manovia_ml/data/toy.py"] == "modified"


def test_a_deleted_dependency_also_fails() -> None:
    """The lock naming a file the commit does not contain is the same class of lie."""

    moved = changed_deps({"toy_split": [{"changed deps": {"src/gone.py": "deleted"}}]})

    assert moved == {"toy_split": {"src/gone.py": "deleted"}}


def test_outputs_and_deps_in_the_same_stage_do_not_mask_each_other() -> None:
    """The real failure looked exactly like this: both entries under one stage."""

    moved = changed_deps(STALE_LOCK)

    assert "changed outs" not in moved["toy_split"]
    assert moved["toy_split"] == {"src/manovia_ml/data/toy.py": "modified"}
