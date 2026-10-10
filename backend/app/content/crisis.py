"""Load and validate ``helplines.json`` — the only file that carries crisis contacts.

AGENTS.md safety rule 6: helpline data lives here, and every entry carries a
``source_url`` (where a person checked it) and a ``last_verified`` date. Nothing
in this module is generated, fetched or user-editable; changing a number means
editing the JSON, re-checking it against the source, and moving the date.

Two layers of validation, because a wrong number is worse than no number:

* the **JSON schema** (``helplines.schema.json``) is the published contract —
  field names, types, the dialable-number pattern, "directories have no number",
  "unverified entries must explain why". The test suite validates the shipped
  file against it, and it can be run against any candidate file before merge.
* the **Pydantic models** below are what the API actually serves, plus the
  invariants a schema cannot express (unique ids, every declared region has an
  emergency number, ``DEFAULT`` always exists so nobody is ever served nothing).

The loader fails loudly. A broken helpline file must be a startup/config error,
never a silently truncated list shown to someone in distress.
"""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from importlib.resources import files
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, HttpUrl, ValidationError, computed_field, field_validator

HELPLINES_FILE: Final = "helplines.json"
HELPLINES_SCHEMA_FILE: Final = "helplines.schema.json"

#: The region served when the requested one is unknown. ``DEFAULT`` carries the
#: international emergency number and the two global directories, so an
#: unrecognised region still gets something dialable — the endpoint never 404s.
FALLBACK_REGION: Final = "DEFAULT"

#: What may appear in a phone or SMS number: the characters a person would
#: actually dial. Anything else is a typo, or something that should not be in
#: this file at all. (The schema carries the same rule.)
_NUMBER_RE: Final = re.compile(r"^[0-9+()\-. ]{3,32}$")

ContactType = Literal["call", "text", "chat", "web"]
ResourceKind = Literal["emergency", "crisis_line", "text_line", "directory"]


class CrisisResource(BaseModel):
    """One place a person in distress can reach."""

    id: str = Field(min_length=3, max_length=64)
    region: str = Field(min_length=2, max_length=16)
    #: ``emergency`` sorts first: someone in immediate danger needs 112/911/999/000
    #: above every counselling line.
    kind: ResourceKind
    name: str = Field(min_length=2, max_length=160)
    #: The number to dial or text, exactly as it should be displayed.
    number: str | None = None
    #: How the entry is reached. ``web`` is reserved for directory entries.
    type: ContactType
    #: The word a text line expects first (``HOME``, ``SHOUT``, ``CONNECT``).
    text_keyword: str | None = Field(default=None, max_length=32)
    #: Anything the caller must know to get through ("dial 988, then press 1").
    instructions: str | None = Field(default=None, max_length=400)
    hours: str = Field(min_length=1, max_length=80)
    languages: list[str] = Field(min_length=1)
    audience: str | None = Field(default=None, max_length=120)
    description: str = Field(min_length=10, max_length=500)
    url: HttpUrl | None = None
    #: Where the number, hours and languages were checked. Never optional.
    source_url: HttpUrl
    #: When a person last checked this entry (the file has its own date too).
    last_verified: date
    #: True when any field rests on a secondary or conflicting source.
    needs_verification: bool = False
    #: Required by the schema whenever ``needs_verification`` is true.
    verification_note: str | None = Field(default=None, max_length=600)
    #: Lower sorts first.
    priority: int = Field(ge=0, le=100)

    @field_validator("number")
    @classmethod
    def _check_dialable(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _NUMBER_RE.match(value):
            raise ValueError("may only contain characters you can dial")
        return value

    @field_validator("languages")
    @classmethod
    def _check_languages(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("languages may not contain blank entries")
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tel_href(self) -> str | None:
        """A ``tel:`` URL for the card, or ``None`` when it cannot be dialled.

        Only digits and a leading ``+`` survive, which is all a ``tel:`` URL may
        carry; the display formatting stays in :attr:`number`. Computed — not
        stored in the JSON — so the dialling format lives in exactly one place and
        every client, including the tests, sees the same string.
        """
        if self.number is None or self.type != "call":
            return None
        cleaned = re.sub(r"[^\d+]", "", self.number)
        return f"tel:{cleaned}" if cleaned else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sms_href(self) -> str | None:
        """An ``sms:`` URL for a text line, or ``None``."""
        if self.number is None or self.type != "text":
            return None
        cleaned = re.sub(r"[^\d+]", "", self.number)
        return f"sms:{cleaned}" if cleaned else None


class HelplineContent(BaseModel):
    """The shipped helpline set, exactly as it is served."""

    version: Literal[2] = 2
    #: When a person last reviewed the file as a whole.
    last_verified: date
    source: str = Field(min_length=20, max_length=1000)
    disclaimer: str = Field(min_length=10, max_length=600)
    regions: list[str] = Field(min_length=1)
    resources: list[CrisisResource] = Field(min_length=1)

    def known_regions(self) -> tuple[str, ...]:
        """Every region declared in the file, in declaration order."""
        return tuple(self.regions)

    def resolve_region(self, region: str | None) -> tuple[str, bool]:
        """Map a request's region onto a served one.

        Returns ``(region, fallback_used)``. An unknown or missing region is
        served ``DEFAULT`` rather than rejected: someone in trouble who typed the
        wrong country code still needs a number.
        """
        candidate = (region or "").strip().upper()
        if candidate in self.regions:
            return candidate, False
        return FALLBACK_REGION, True

    def resources_for(self, region: str | None = None) -> list[CrisisResource]:
        """Entries for one region, sorted by priority then id.

        ``DEFAULT`` entries are appended to every region, because the global
        directories are useful everywhere and a thin region must never look
        empty. The ``DEFAULT`` emergency number is dropped when the region has
        its own — a card that shows 112 above 911 in the US is worse than one
        that shows 911 alone.
        """
        resolved, _ = self.resolve_region(region)
        selected = [item for item in self.resources if item.region == resolved]
        if resolved != FALLBACK_REGION:
            has_emergency = any(item.kind == "emergency" for item in selected)
            selected += [
                item
                for item in self.resources
                if item.region == FALLBACK_REGION
                and not (has_emergency and item.kind == "emergency")
            ]
        return sorted(selected, key=lambda item: (item.priority, item.id))

    def emergency_for(self, region: str | None = None) -> CrisisResource | None:
        """The emergency number for a region, falling back to ``DEFAULT``."""
        resolved, _ = self.resolve_region(region)
        for candidate in (resolved, FALLBACK_REGION):
            for item in self.resources:
                if item.region == candidate and item.kind == "emergency" and item.number:
                    return item
        return None

    def unverified(self) -> list[CrisisResource]:
        """Every entry flagged ``needs_verification`` — the human to-do list."""
        return [item for item in self.resources if item.needs_verification]


def _check_invariants(content: HelplineContent) -> None:
    """Rules the schema cannot express, checked before anything is served."""
    ids = [resource.id for resource in content.resources]
    if len(set(ids)) != len(ids):
        duplicates = sorted({resource_id for resource_id in ids if ids.count(resource_id) > 1})
        raise ValueError(f"helplines.json has duplicate resource ids: {duplicates}")

    if FALLBACK_REGION not in content.regions:
        raise ValueError(f"helplines.json must declare a {FALLBACK_REGION} region")

    declared = set(content.regions)
    undeclared = sorted({resource.region for resource in content.resources} - declared)
    if undeclared:
        raise ValueError(f"resources declared for unknown regions: {undeclared}")

    unreachable = [
        resource.id
        for resource in content.resources
        if resource.number is None and resource.url is None
    ]
    if unreachable:
        raise ValueError(f"resources with no way to make contact: {unreachable}")

    missing_emergency = sorted(
        region
        for region in declared
        if not any(
            resource.region == region and resource.kind == "emergency" and resource.number
            for resource in content.resources
        )
    )
    if missing_emergency:
        raise ValueError(f"regions with no emergency number: {missing_emergency}")

    for resource in content.resources:
        if resource.needs_verification and not (resource.verification_note or "").strip():
            raise ValueError(f"{resource.id} is flagged needs_verification but explains nothing")


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


@lru_cache(maxsize=1)
def load_helplines_schema() -> dict[str, Any]:
    """The published JSON schema for :data:`HELPLINES_FILE`."""
    raw = files("app.content").joinpath(HELPLINES_SCHEMA_FILE).read_text(encoding="utf-8")
    schema: dict[str, Any] = json.loads(raw)
    return schema
