"""Fixtures for the safety suite: one engine, one escalator, and the case table.

The engine is built once per session. That is deliberate, and it is also the
point: a rules engine whose answer depends on anything but the text is not a
rules engine. Every case in ``cases.yaml`` therefore runs against exactly the
same compiled pattern set, and a data change shows up as case failures rather
than as flaky behaviour.

The case file is validated on load (unique ids, known levels, known categories,
non-empty text) so a typo in a case cannot silently turn into a test that asserts
nothing.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from pydantic import BaseModel, Field, field_validator

from app.services.safety.base import RiskCategory, RiskLevel
from app.services.safety.escalation import Escalator, build_escalator
from app.services.safety.rules import RuleEngine, build_engine

CASES_FILE: Final = Path(__file__).parent / "cases.yaml"


class SafetyCase(BaseModel):
    """One row of the case table: an input and what the engine must do with it."""

    model_config = {"frozen": True}

    #: Stable id, used as the pytest id so a failure names the case.
    id: str = Field(min_length=3, max_length=64)
    #: Which hard category the case exercises ("negation", "leetspeak", …).
    group: str = Field(min_length=2, max_length=48)
    #: Synthetic text. Never a real person's words, never a method.
    text: str = Field(min_length=1)
    #: The level the engine must assign.
    level: RiskLevel
    #: When given, the exact set of categories — not merely a subset.
    categories: tuple[RiskCategory, ...] | None = None
    #: Rationale codes that must be present.
    codes: tuple[str, ...] = ()
    #: Rationale codes that must be absent.
    forbid_codes: tuple[str, ...] = ()
    #: When given, the required value of the third-person judgement.
    third_person: bool | None = None
    #: When given, the required value of the first-person judgement.
    first_person: bool | None = None
    #: Why this case exists. Shown in failure output.
    note: str | None = None

    @field_validator("level", mode="before")
    @classmethod
    def _parse_level(cls, value: Any) -> Any:
        return RiskLevel[value.strip().upper()] if isinstance(value, str) else value

    @field_validator("categories", mode="before")
    @classmethod
    def _parse_categories(cls, value: Any) -> Any:
        if value is None:
            return None
        return tuple(RiskCategory(item) for item in value)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a case must contain something other than whitespace")
        return value


@lru_cache(maxsize=1)
def load_cases() -> tuple[SafetyCase, ...]:
    """Read and validate ``cases.yaml`` once per session."""
    payload = yaml.safe_load(CASES_FILE.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("cases.yaml must be a mapping")

    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases.yaml must carry a non-empty 'cases' list")

    cases = tuple(SafetyCase.model_validate(item) for item in raw_cases)
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise ValueError(f"cases.yaml has duplicate ids: {duplicates}")
    texts = [case.text for case in cases]
    duplicate_texts = sorted({text for text in texts if texts.count(text) > 1})
    if duplicate_texts:
        raise ValueError(f"cases.yaml repeats the same text: {duplicate_texts}")
    return cases


def case_ids() -> list[str]:
    """Every case id, for parametrising tests."""
    return [case.id for case in load_cases()]


@pytest.fixture(scope="session")
def engine() -> RuleEngine:
    """The engine under test, over the shipped pattern data."""
    return build_engine()


@pytest.fixture(scope="session")
def escalator() -> Escalator:
    """The escalation policy, over the shipped helpline and template content."""
    return build_escalator()


@pytest.fixture(scope="session")
def cases() -> tuple[SafetyCase, ...]:
    """The whole case table."""
    return load_cases()


@pytest.fixture(scope="session")
def case_by_id() -> dict[str, SafetyCase]:
    """The case table, indexed by id."""
    return {case.id: case for case in load_cases()}
