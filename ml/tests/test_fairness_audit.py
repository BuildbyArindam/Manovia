"""The fairness gate has to be a gate: stable numbers, thresholds that apply.

Two failures found on Day 49 while running the v2.0 verification on a fresh
clone, both invisible on the day the code was written:

* ``ml/reports/fairness.json`` differed between two identical runs. The
  stand-in predictors seeded themselves from Python's built-in ``hash()``,
  which is salted per process (PEP 456). Cross-process determinism cannot be
  observed from inside one process, so the test below runs the *script* twice
  with two different ``PYTHONHASHSEED`` values and compares the bytes.
* ``max_gap_code_mixing`` in ``release_gates.yaml`` never matched the
  ``code-mixing`` category the templates use, so that dimension silently ran
  against the global 0.15 default instead of its configured 0.12.

Neither is about the size of a gap; both are about whether a red build means
anything.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from manovia_ml.fairness.audit import (
    _load_thresholds,
    _normalise_category,
    _stable_int,
    audit_all_fake,
)
from manovia_ml.paths import ml_root

CONFIG = ml_root() / "configs" / "release_gates.yaml"


def test_stable_int_does_not_depend_on_the_process() -> None:
    assert _stable_int("code-mixing-hinglish") == _stable_int("code-mixing-hinglish")
    assert _stable_int("a") != _stable_int("b")


def test_every_configured_category_ceiling_is_actually_applied() -> None:
    thresholds = _load_thresholds(CONFIG)

    # The category names the templates use, verbatim.
    assert thresholds["religion"] == 0.08
    assert thresholds["caste"] == 0.08
    assert thresholds["code-mixing"] == 0.12
    assert thresholds["dialect-spelling"] == 0.12
    assert thresholds["region"] == 0.10


def test_no_template_falls_back_to_the_global_default() -> None:
    """A dimension that quietly uses ``max_gap`` is a dimension with no ceiling.

    The global default is the loosest number in the file, so a mismatch is
    always in the permissive direction — the failure mode a gate must not have.
    """

    from manovia_ml.fairness.templates import TEMPLATES

    thresholds = _load_thresholds(CONFIG)
    missing = sorted(
        {t.category for t in TEMPLATES if _normalise_category(t.category) not in thresholds}
    )

    assert not missing, f"these categories have no configured ceiling: {missing}"


def test_audit_all_fake_is_byte_stable_across_processes(tmp_path: Path) -> None:
    reports: list[bytes] = []
    for hashseed in ("0", "1"):
        out = tmp_path / f"fairness-{hashseed}.json"
        env = {**os.environ, "PYTHONHASHSEED": hashseed}
        subprocess.run(
            [
                sys.executable,
                str(ml_root() / "scripts" / "check_fairness_gates.py"),
                "--report",
                str(out),
                "--config",
                str(CONFIG),
            ],
            check=True,
            cwd=ml_root().parent,
            env=env,
            capture_output=True,
        )
        reports.append(out.read_bytes())

    assert reports[0] == reports[1], (
        "the gate report changed between two processes; a score seeded from hash() "
        "cannot be reproduced"
    )

    payload = json.loads(reports[0])
    assert payload["overall_passed"] is True


def test_audit_all_fake_reports_every_model() -> None:
    result = audit_all_fake(config_path=CONFIG)

    assert result["total_models"] == 5
    assert set(result["models"]) == {
        "safety_crisis",
        "emotion_joy",
        "emotion_sadness",
        "retrieval_relevance",
        "safety_confidence",
    }
    assert result["largest_gap_overall"] is not None
    assert result["largest_gap_overall"]["max_gap"] < 0.08
