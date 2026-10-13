"""Make the red-team library in ``evals/redteam`` importable from the tests.

Same approach as ``tests/safety/test_eval_harness.py``: the suite lives with the
other evals, and the tests import it rather than copying its cases.
"""

from __future__ import annotations

import sys
from pathlib import Path

REDTEAM_DIR = Path(__file__).resolve().parents[3] / "evals" / "redteam"
if str(REDTEAM_DIR) not in sys.path:
    sys.path.insert(0, str(REDTEAM_DIR))
