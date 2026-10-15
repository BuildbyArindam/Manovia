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

import hashlib
from pathlib import Path
from typing import Any

import yaml

from manovia_ml.data.dvc_lock import changed_deps
from manovia_ml.paths import ml_root, resolve

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


# --- The committed lock itself, not only the parsing of `dvc status` --------
#
# Everything above tests `changed_deps` as a pure function over hand-written
# dictionaries. That is the right unit test, and it is also why Day 35 shipped a
# stale lock: `ml/dvc.lock` recorded md5 `197dd987…` for
# `src/manovia_ml/retrieval/data.py` while the committed file hashed to
# `7bcaa72b…`, because `eval_set_ref` was added *after* `dvc repro` had written
# the lock and the lock was never regenerated. The full ml suite passed locally;
# only CI caught it, at step 5 of `ml-smoke`, which then skipped every later
# step. These two tests ask the same question the CI step asks — "does the lock
# describe the code in this commit?" — directly from the files, with no dvc
# subprocess, so the mistake is caught by `make -C ml test` before a commit.

LOCK_PATH = ml_root() / "dvc.lock"
PIPELINE_PATH = ml_root() / "dvc.yaml"

#: The dependency whose stale hash broke CI. Asserted explicitly so that a bug in
#: the path handling cannot turn this test into a vacuous pass.
CANARY = "src/manovia_ml/retrieval/data.py"


def _stages(path: Path) -> dict[str, Any]:
    """Parse a DVC YAML document into its `{stage: entry}` mapping."""

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    stages: Any = document.get("stages") if isinstance(document, dict) else None
    return dict(stages) if isinstance(stages, dict) else {}


def _file_deps(stages: dict[str, Any]) -> list[tuple[str, str, str, int]]:
    """Every plain-file dependency of the lock, as `(stage, path, md5, size)`.

    Directory dependencies are skipped: DVC records those as a `.dir` hash over
    the whole tree, and reproducing that algorithm is not this test's job. Files
    that are not on disk are skipped as well — gitignored data inputs such as
    `data/retrieval/expanded.jsonl` are absent on a fresh clone, and "not built
    here yet" is never a failure. That is the same distinction `changed_deps`
    draws between `changed outs` and `changed deps`.
    """

    found: list[tuple[str, str, str, int]] = []
    for stage, entry in sorted(stages.items()):
        deps: Any = entry.get("deps") if isinstance(entry, dict) else None
        for dep in deps if isinstance(deps, list) else []:
            if not isinstance(dep, dict):
                continue
            recorded = dep.get("md5")
            if not isinstance(recorded, str) or recorded.endswith(".dir"):
                continue
            path = str(dep.get("path", ""))
            if not path or not resolve(path).is_file():
                continue
            found.append((stage, path, recorded, int(dep.get("size", -1))))
    return found


def test_the_committed_lock_re_hashes_to_the_code_in_this_commit() -> None:
    """Every recorded dependency hash must match the file as it exists now."""

    deps = _file_deps(_stages(LOCK_PATH))
    checked = {path for _, path, _, _ in deps}

    assert CANARY in checked, f"{CANARY} is not hashed by this test; it has gone vacuous"
    assert len(checked) >= 10, f"only {len(checked)} dependencies checked; expected the real lock"

    stale: list[str] = []
    for stage, path, recorded_md5, recorded_size in deps:
        blob = resolve(path).read_bytes()
        # DVC's content hash, not a security digest — md5 is what the lock records.
        actual_md5 = hashlib.md5(blob).hexdigest()
        if actual_md5 != recorded_md5 or len(blob) != recorded_size:
            stale.append(
                f"  {stage}: {path} — lock says {recorded_md5}/{recorded_size} bytes, "
                f"disk says {actual_md5}/{len(blob)} bytes"
            )

    assert not stale, (
        "ml/dvc.lock is stale: these dependencies changed since the lock was written.\n"
        + "\n".join(stale)
        + "\n\nRun `make -C ml data` and commit the regenerated ml/dvc.lock."
    )


def test_every_pipeline_stage_is_recorded_in_the_lock() -> None:
    """A stage in `dvc.yaml` with no lock entry is the same class of stale lock.

    `changed_deps` cannot see this one: DVC reports a never-run stage as missing
    *outputs*, which the CI check deliberately tolerates for a fresh clone, so
    `dvc repro` would silently add the entry and the drift would go unnoticed.
    """

    missing = sorted(set(_stages(PIPELINE_PATH)) - set(_stages(LOCK_PATH)))

    assert not missing, (
        f"ml/dvc.yaml declares stages that ml/dvc.lock does not record: {missing}. "
        "Run `make -C ml data` and commit the regenerated lock."
    )
