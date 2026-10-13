"""Is `dvc.lock` still an honest record of how this data was produced?

`dvc status` distinguishes two very different kinds of drift, and only one of them
is a problem with the repository:

* **changed outs** — the outputs are not on disk (deleted, or never built on this
  machine). That is the normal state of a fresh clone: the splits are gitignored
  and rebuilt by `dvc repro`, so it must never fail a check.
* **changed deps** — an input's hash differs from the one `dvc.lock` recorded. On
  a fresh clone that can only mean the *committed* lock is stale: the code moved
  and the pipeline was not re-run. This is exactly what happened on Day 31 — a
  lint fix to ``src/manovia_ml/data/toy.py`` changed its md5 while the committed
  lock still named the old file — and CI could not see it, because the job runs
  `dvc repro` first and repro rewrites the lock before anybody looks at it.

So this module exists to be run *before* `dvc repro`, looking only at the
dependencies. It is the difference between "this data has not been built here
yet" (fine) and "this repository is describing a pipeline it does not contain"
(not fine).

    python -m manovia_ml.data.dvc_lock
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

__all__ = ["changed_deps", "main"]


def changed_deps(status: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Extract ``{stage: {dependency: state}}`` for every dependency that moved.

    Accepts the parsed output of ``dvc status --json``. Kept separate from the
    CLI so the decision is unit-tested without needing a DVC repository (and
    without a network, a cache, or two minutes of subprocess).
    """

    moved: dict[str, dict[str, str]] = {}
    for stage, entries in sorted(status.items()):
        for entry in entries:
            for path, state in sorted((entry.get("changed deps") or {}).items()):
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

    moved = changed_deps(status)
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

    stages = ", ".join(sorted(status)) or "none"
    print(f"dvc.lock is consistent with the code (stages: {stages}; missing outputs are expected)")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI
    raise SystemExit(main())
