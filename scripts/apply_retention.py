"""Day 22 CLI: enforce every person's retention window against a real database.

Thin wrapper so the job can be run the same way as the other repository scripts
(from the repository root, no installation step). The work itself lives in
``app.services.privacy.retention``, which is also what the test suite exercises
in-process — this file adds nothing but the path fix and the exit code.

Usage:

    backend/.venv/bin/python scripts/apply_retention.py --dry-run   # report only
    backend/.venv/bin/python scripts/apply_retention.py             # delete
    backend/.venv/bin/python scripts/apply_retention.py --json      # machine-readable
    make retention DRY_RUN=1

A dry run is strongly recommended first: it prints exactly which tables and how
many rows each person would lose, and writes nothing. The report contains
counts, table names and cutoff timestamps — never a person's text or their id.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.privacy.retention import main  # noqa: E402  (path fix above)

if __name__ == "__main__":
    sys.exit(main())
