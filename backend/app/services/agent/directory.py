"""The curated support directory: what the agent may search.

The file lives at ``app/content/resources/directory.json`` and is the
only source of support options — there is no web access, no live
lookup, nothing fetched at runtime. AGENTS.md safety rule 6's spirit
applies here too: every entry names the official source it was
verified against and the date a person checked it.

Two guarantees this module must hold:

* **``needs_verification`` entries never reach a person.** They stay in
  the file (so a human can see what is missing) but every search
  excludes them, and the only way they surface is the operator's
  ``excluded()`` listing.
* **Directory text is data, never markup or commands.** Entry text is
  sanitised on load *and* again on every tool result, so an entry that
  somehow smuggles in control characters or instruction-shaped prose
  cannot reach the model or the UI raw.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, model_validator

from app.services.agent.sanitise import sanitise_text

#: Budgets a person may name. ``low_cost`` admits free options too —
#: a person on zero budget wants everything that costs nothing.
Budget = str  #: ``free`` | ``low_cost`` | ``any``

#: How many matches one search returns at most. A directory is a menu,
#: not a scroll.
SEARCH_LIMIT = 8

_DIRECTORY_FILE = "directory.json"
_DIRECTORY_SCHEMA_FILE = "directory.schema.json"


class DirectoryEntry(BaseModel):
    """One verified (or explicitly unverified) support option."""

    id: str = Field(min_length=3, max_length=64)
    regions: list[str] = Field(min_length=1)
    kind: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(min_length=10, max_length=500)
    contact: dict[str, Any] = Field(default_factory=dict)
    languages: list[str] = Field(min_length=1)
    multilingual: bool = False
    language_note: str | None = Field(default=None, max_length=200)
    cost: str = Field(min_length=1, max_length=32)
    cost_note: str | None = Field(default=None, max_length=200)
    hours: str = Field(min_length=1, max_length=120)
    needs: list[str] = Field(min_length=1)
    audience: str | None = Field(default=None, max_length=120)
    source_url: str = Field(min_length=8, max_length=512)
    last_verified: date
    verification_status: str = Field(min_length=1, max_length=32)
    verification_note: str | None = Field(default=None, max_length=600)

    @model_validator(mode="after")
    def _status_and_note_agree(self) -> DirectoryEntry:
        if self.verification_status not in {"verified", "needs_verification"}:
            raise ValueError(f"unknown verification_status {self.verification_status!r}")
        if self.verification_status == "needs_verification" and not self.verification_note:
            raise ValueError("an entry marked needs_verification must say why")
        if self.verified and not self.contact:
            raise ValueError("a verified entry must carry a contact channel")
        return self

    @property
    def verified(self) -> bool:
        return self.verification_status == "verified"

    def sanitised(self) -> dict[str, Any]:
        """The entry as tool output: every string field sanitised."""
        return {
            "id": self.id,
            "name": sanitise_text(self.name),
            "kind": self.kind,
            "regions": self.regions,
            "description": sanitise_text(self.description),
            "contact": {
                key: value for key, value in self.contact.items() if isinstance(value, (str, list))
            },
            "languages": self.languages,
            "multilingual": self.multilingual,
            "cost": self.cost,
            "cost_note": sanitise_text(self.cost_note) if self.cost_note else None,
            "hours": sanitise_text(self.hours),
            "needs": self.needs,
            "source_url": self.source_url,
            "last_verified": self.last_verified.isoformat(),
        }


class DirectoryData(BaseModel):
    """The whole file, as validated by the JSON schema first."""

    version: int
    last_verified: date
    source: str
    disclaimer: str
    regions: list[str]
    resources: list[DirectoryEntry]

    @property
    def verified(self) -> list[DirectoryEntry]:
        return [entry for entry in self.resources if entry.verified]

    @property
    def excluded(self) -> list[DirectoryEntry]:
        """Entries held out for re-verification. Never served to anyone."""
        return [entry for entry in self.resources if not entry.verified]


def load_directory_schema() -> dict[str, Any]:
    """The published contract for ``directory.json``."""
    raw = (
        files("app.content.resources").joinpath(_DIRECTORY_SCHEMA_FILE).read_text(encoding="utf-8")
    )
    parsed: dict[str, Any] = json.loads(raw)
    return parsed


def _validate_against_schema(payload: dict[str, Any]) -> None:
    """Fail loudly: a broken directory is a startup error, not a blank menu."""
    schema = load_directory_schema()
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=str)
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise ValueError(f"directory.json fails its schema at {location}: {first.message}")


class DirectoryStore:
    """Read-only access to the curated directory.

    Searching is pure filtering over committed, verified entries — no
    network, no ranking model, no surprises. An unrecognised region or
    language matches nothing rather than everything: showing a person a
    global list when they asked for their own region is worse than
    saying the directory has nothing verified there yet.
    """

    def __init__(self, data: DirectoryData) -> None:
        self._data = data

    @classmethod
    def load(cls, path: Path | None = None) -> DirectoryStore:
        """Load and validate the shipped file (or a test fixture)."""
        if path is None:
            raw = (
                files("app.content.resources").joinpath(_DIRECTORY_FILE).read_text(encoding="utf-8")
            )
            payload = json.loads(raw)
        else:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        _validate_against_schema(payload)
        return cls(DirectoryData(**payload))

    @property
    def data(self) -> DirectoryData:
        return self._data

    def excluded(self) -> list[DirectoryEntry]:
        """Entries excluded as ``needs_verification`` (operator view only)."""
        return self._data.excluded

    def search(
        self,
        *,
        region: str | None = None,
        language: str | None = None,
        need: str | None = None,
        budget: str | None = None,
        limit: int = SEARCH_LIMIT,
    ) -> list[dict[str, Any]]:
        """Verified entries matching every filter, most local first.

        ``multilingual`` entries satisfy any language ask — their own
        claim is that they serve many languages, which is exactly what
        the filter is testing. A sub-national region (``IN-WB``) also
        matches its country's entries (``IN``): a person in Kolkata
        should see the national lines that serve them, not only
        Kolkata-specific ones.
        """
        wanted_regions = self._expand_region(region)
        matches: list[tuple[int, DirectoryEntry]] = []
        for entry in self._data.verified:
            if wanted_regions is not None and not (
                wanted_regions & set(entry.regions) or "GLOBAL" in entry.regions
            ):
                continue
            if language is not None and not (language in entry.languages or entry.multilingual):
                continue
            if (
                need is not None
                and need != "any"
                and not (need in entry.needs or "any" in entry.needs)
            ):
                continue
            if budget == "free" and entry.cost != "free":
                continue
            if budget == "low_cost" and entry.cost not in {"free", "low_cost"}:
                continue
            specificity = 0 if "GLOBAL" in entry.regions else 1
            matches.append((specificity, entry))
        matches.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [entry.sanitised() for _, entry in matches[:limit]]

    @staticmethod
    def _expand_region(region: str | None) -> set[str] | None:
        """The region plus its country (``IN-WB`` → both)."""
        if region is None:
            return None
        candidates = {region}
        if "-" in region:
            candidates.add(region.split("-", 1)[0])
        return candidates


@lru_cache(maxsize=1)
def load_directory() -> DirectoryStore:
    """The shipped directory, parsed and validated once."""
    return DirectoryStore.load()


@dataclass(frozen=True)
class DirectoryFacts:
    """Operator summary for health endpoints and tests."""

    total: int
    verified: int
    excluded: int
    regions: list[str]

    @classmethod
    def from_store(cls, store: DirectoryStore) -> DirectoryFacts:
        return cls(
            total=len(store.data.resources),
            verified=len(store.data.verified),
            excluded=len(store.data.excluded),
            regions=list(store.data.regions),
        )
