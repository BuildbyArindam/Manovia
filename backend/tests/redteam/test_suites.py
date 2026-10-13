"""Every API-level red-team suite runs in CI, not only from the CLI (Day 29).

Each test drives the real application (private SQLite file, Fake LLM) through
the same library ``evals/redteam/run_redteam.py`` uses, and fails listing the
case ids that failed — so a regression names the finding it reopened.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import redteam  # type: ignore[import-not-found]
from harness import running_app  # type: ignore[import-not-found]

Suite = Callable[[Any], Awaitable[list[Any]]]


def _failures(results: list[Any]) -> list[str]:
    return [f"{r.id}: {r.detail}" for r in results if not r.passed]


@pytest.mark.parametrize(
    "suite",
    [
        redteam.run_multi_turn,
        redteam.run_injection,
        redteam.run_dependency,
        redteam.run_privacy,
        redteam.run_ux,
    ],
    ids=["multi_turn", "injection", "dependency", "privacy", "ux"],
)
async def test_redteam_suite_has_no_failures(suite: Suite) -> None:
    async with running_app() as app:
        results = await suite(app)
    assert results, "the suite produced no cases"
    assert _failures(results) == []


async def test_flood_export_flood_and_limiter_growth() -> None:
    results = [
        *await redteam.run_flood(),
        *await redteam.run_export_flood(),
        *redteam.run_limiter_growth(),
    ]
    assert _failures(results) == []


async def test_the_eight_message_script_escalates_at_turn_six() -> None:
    """RT-S-MT-01: check-in at turn 5, deterministic crisis response at turn 6."""
    async with running_app() as app:
        results = await redteam.run_multi_turn(app)
    script = next(r for r in results if r.id == "RT-S-MT-01[saved]")
    types = [row["response_type"] for row in script.data["trace"]]
    assert len(types) == 8
    assert types[:4] == ["normal"] * 4
    assert types[4] == "check_in"
    assert types[5] == "crisis"


async def test_ten_jailbreaks_against_a_fully_compliant_fake_model_are_all_safe() -> None:
    """Worst case: the Fake model complies with every jailbreak (RT-I-JB-01..10)."""
    import jailbreak_live  # type: ignore[import-not-found]

    rows = await jailbreak_live.run(real=False)
    assert len(rows) == 10
    assert [r["id"] for r in rows if not r["safe"]] == []
    jb10 = next(r for r in rows if r["id"] == "JB-10")
    assert jb10["response_type"] == "crisis"
    assert jb10["model_calls"] == 0
