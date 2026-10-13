"""Day 29 regression gate: every single-message safety-bypass case, by RT id.

The cases live in ``evals/redteam/cases/safety_bypass.yaml``; this file runs
each one through the shipped rules engine (no ML — the deterministic layer is
what AGENTS.md rule 1 relies on) and fails with the case id. A case that
regresses names itself in the test id, e.g. ``test_case[RT-S-EU-03]``.
"""

from __future__ import annotations

from typing import Any

import pytest
import redteam  # type: ignore[import-not-found]

_CASES: list[dict[str, Any]] = redteam.load_cases("safety_bypass")["cases"]
_RESULTS: dict[str, Any] = {result.id: result for result in redteam.run_safety_bypass()}


@pytest.mark.parametrize("case_id", [case["id"] for case in _CASES])
def test_case(case_id: str) -> None:
    result = _RESULTS[case_id]
    assert result.passed, f"{case_id}: {result.detail} {result.data}"


def test_every_category_from_the_brief_is_covered() -> None:
    categories = {case["category"] for case in _CASES}
    assert {
        "euphemism",
        "spelling",
        "other_language",
        "mixed_language",
        "long_buried",
        "quoted",
        "sarcasm",
    } <= categories


def test_controls_exist_for_the_new_slang_rules() -> None:
    """Benign look-alikes must be in the suite, or precision is unmeasured."""
    controls = [case for case in _CASES if case.get("expect_max") == "none"]
    assert len(controls) >= 3
