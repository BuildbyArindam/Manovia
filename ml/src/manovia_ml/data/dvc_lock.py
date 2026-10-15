"""Is `dvc.lock` still an honest record of how this data was produced?

`dvc status` distinguishes several kinds of drift, and only some of them are a
problem with the repository:

* **changed outs** — the outputs are not on disk (deleted, or never built on this
  machine). That is the normal state of a fresh clone: the splits are gitignored
  and rebuilt by `dvc repro`, so it must never fail a check.
* **changed deps, an upstream stage's output** — also the normal state of a fresh
  clone, and the case this module was originally wrong about. Day 35 added the
  pipeline's first *chained* stages: `retrieval_split` consumes
  `data/retrieval/expanded.jsonl`, which `retrieval_expand` produces and which is
  gitignored. On a clone that file is legitimately absent, `dvc status` reports it
  as a changed dep, and `dvc repro` builds it in the right order. Failing here
  would fail every fresh clone forever, so a dep that is declared as some stage's
  output is skipped — but only while it is *missing*, never when it differs.
* **changed deps, anything else** — an input's hash differs from the one
  `dvc.lock` recorded, or a file the commit is supposed to contain has gone. On a
  fresh clone that can only mean the *committed* lock is stale: the code moved and
  the pipeline was not re-run. This is exactly what happened on Day 31 — a lint fix
  to ``src/manovia_ml/data/toy.py`` changed its md5 while the committed lock still
  named the old file — and again on Day 35, when ``retrieval/data.py`` grew
  ``eval_set_ref`` after ``dvc repro`` had written the lock. CI could not see the
  Day-31 one because the job ran `dvc repro` first and repro rewrites the lock
  before anybody looks at it.

So this module exists to be run *before* `dvc repro`. It is the difference between
"this data has not been built here yet" (fine) and "this repository is describing
a pipeline it does not contain" (not fine).

    python -m manovia_ml.data.dvc_lock
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from manovia_ml.paths import ml_root

__all__ = ["changed_deps", "main", "pipeline_outputs"]

#: Relative path of the pipeline definition, from the ML project root.
PIPELINE_FILENAME = "dvc.yaml"


def pipeline_outputs(pipeline: Path | None = None) -> set[str]:
    """Every path declared as an ``outs`` entry by any stage in ``dvc.yaml``.

    These are the dependencies that a fresh clone legitimately does not have yet,
    because another stage in the same pipeline builds them. Read from the pipeline
    file rather than from the lock so that the answer does not depend on the lock
    being correct — the lock is what is under test here.
    """

    path = pipeline if pipeline is not None else ml_root() / PIPELINE_FILENAME
    if not path.is_file():
        return set()

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    stages: Any = document.get("stages") if isinstance(document, dict) else None
    outputs: set[str] = set()
    for entry in stages.values() if isinstance(stages, dict) else []:
        outs: Any = entry.get("outs") if isinstance(entry, dict) else None
        for out in outs if isinstance(outs, list) else []:
            name = out.get("path") if isinstance(out, dict) else out
            if isinstance(name, str) and name.strip():
                outputs.add(name.strip())
    return outputs


def changed_deps(
    status: dict[str, Any],
    known_outputs: Iterable[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Extract ``{stage: {dependency: state}}`` for every dependency that moved.

    Accepts the parsed output of ``dvc status --json``. Kept separate from the CLI
    so the decision is unit-tested without needing a DVC repository (and without a
    network, a cache, or two minutes of subprocess).

    ``known_outputs`` are paths another stage of the same pipeline produces. A
    dependency in that set is skipped **only** when its state is ``deleted`` —
    an upstream output that has not been built on this machine yet. A dependency
    that is present but *differs* is never skipped, whatever produces it: if a
    stage's output no longer hashes to what the lock recorded, the pipeline is not
    reproducible and that is precisely what this check exists to catch.
    """

    built_elsewhere = set(known_outputs or ())
    moved: dict[str, dict[str, str]] = {}
    for stage, entries in sorted(status.items()):
        for entry in entries:
            for path, state in sorted((entry.get("changed deps") or {}).items()):
                if state == "deleted" and path in built_elsewhere:
                    continue
                moved.setdefault(stage, {})[path] = state
    return moved


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0

    completed = subprocess.run(
        [sys.executable, "-m", "dvc", "status", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr or completed.stdout)
        return completed.returncode or 1

    try:
        status = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        sys.stderr.write(f"could not parse `dvc status --json` output:\n{completed.stdout}\n")
        return 1

    outputs = pipeline_outputs()
    moved = changed_deps(status, outputs)
    if moved:
        print(
            "dvc.lock is out of date — these dependencies changed since it was written:",
            file=sys.stderr,
        )
        for stage, deps in moved.items():
            for path, state in deps.items():
                print(f"  {stage}: {path} ({state})", file=sys.stderr)
        print(
            "\nRun `make ml-data` (dvc repro) and commit the updated ml/dvc.lock.\n"
            "A lock file that disagrees with the code says the pipeline that produced\n"
            "this data is not the pipeline in this commit.",
            file=sys.stderr,
        )
        return 1

    pending = _unbuilt_upstream(status, outputs)
    if pending:
        print(f"not built here yet, and built by an earlier stage: {', '.join(pending)}")
    stages = ", ".join(sorted(status)) or "none"
    print(f"dvc.lock is consistent with the code (stages: {stages}; missing outputs are expected)")
    return 0


def _unbuilt_upstream(status: dict[str, Any], outputs: set[str]) -> list[str]:
    """The upstream outputs this clone has not built, for the log line above."""

    pending: set[str] = set()
    for entries in status.values():
        for entry in entries:
            for path, state in (entry.get("changed deps") or {}).items():
                if state == "deleted" and path in outputs:
                    pending.add(path)
    return sorted(pending)


if __name__ == "__main__":  # pragma: no cover - thin CLI
    raise SystemExit(main())
