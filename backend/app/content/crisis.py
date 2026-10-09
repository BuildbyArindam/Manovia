"""Load and validate ``helplines.json``.

AGENTS.md safety rule 6: helpline data lives only in this file, and it carries a
``last_verified`` date that a person has checked. Nothing here is generated and
nothing here is user-editable — changing a number means editing the JSON and
re-checking the date.

The file is validated on load rather than trusted: a typo in a phone number, a
dead link, or two resources sharing an id would all be worse than a missing
entry, and the loader refuses to serve a set that does not hold together.
"""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from importlib.resources import files
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, ValidationError, field_validator

HELPLINES_FILE = "helplines.json"

#: What may appear in a phone or SMS number: the characters a person would
#: actually dial. Anything else is a typo, or something that should not be in
#: this file at all.
_NUMBER_PATTERN = re.compile(r"^[0-9+()\-.\s]{3,32}$")


class CrisisResource(BaseModel):
    """One place a person in distress can reach."""

    id: str = Field(min_length=1, max_length=64)
    region: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = None
    sms: str | None = None
    url: HttpUrl | None = None
    hours: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=400)
    #: Lower sorts first; the frontend shows the list in this order.
    priority: int = Field(ge=0, le=100)

    @field_validator("phone", "sms")
    @classmethod
    def _check_dialable(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _NUMBER_PATTERN.match(value):
            raise ValueError("may only contain characters you can dial")
        return value


class HelplineContent(BaseModel):
    """The shipped helpline set, exactly as it is served."""

    last_verified: date
    source: str = Field(min_length=1, max_length=400)
    disclaimer: str = Field(min_length=1, max_length=400)
    resources: list[CrisisResource] = Field(min_length=1)


def _check_invariants(content: HelplineContent) -> None:
    """Rules the schema cannot express, checked before anything is served."""
    ids = [resource.id for resource in content.resources]
    if len(set(ids)) != len(ids):
        duplicates = sorted({resource_id for resource_id in ids if ids.count(resource_id) > 1})
        raise ValueError(f"helplines.json has duplicate resource ids: {duplicates}")

    unreachable = [
        resource.id
        for resource in content.resources
        if resource.phone is None and resource.sms is None and resource.url is None
    ]
    if unreachable:
        raise ValueError(f"resources with no way to make contact: {unreachable}")


def parse_helplines(payload: dict[str, Any]) -> HelplineContent:
    """Validate an already-parsed helpline payload.

    Raises ``ValueError`` with the offending details: a broken helpline file
    must be a loud configuration error, never a silently truncated list.
    """
    try:
        content = HelplineContent.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"helplines.json failed validation: {exc.errors()}") from exc
    _check_invariants(content)
    return content


@lru_cache(maxsize=1)
def load_helplines() -> HelplineContent:
    """Parse the shipped helpline set (cached; the file never changes at runtime)."""
    raw = files("app.content").joinpath(HELPLINES_FILE).read_text(encoding="utf-8")
    payload: dict[str, Any] = json.loads(raw)
    return parse_helplines(payload)
