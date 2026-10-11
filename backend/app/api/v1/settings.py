"""Planner preferences: ``GET`` and ``PUT /settings``.

The two knobs the daily planner reads — preferred exercise types and quiet
hours — live here. Values are validated on write against facts the server owns:
exercise tags against the shipped catalogue, quiet-hour bounds against a strict
``HH:MM`` shape. A ``GET`` for a person who never saved returns the defaults
rather than a 404, so the settings screen always has something true to show.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.api.deps import SessionDep, require_consent
from app.db.repos import SettingsRepository
from app.models.enums import ConsentKind
from app.models.user import User
from app.models.user_settings import QUIET_TIME_PATTERN, UserSettings
from app.services.exercises.engine import get_catalog
from app.services.plan.daily_plan import DEFAULT_QUIET_END, DEFAULT_QUIET_START

router = APIRouter(prefix="/settings", tags=["settings"])

SettingsConsentGate = require_consent(ConsentKind.TERMS, ConsentKind.PRIVACY)
SettingsUser = Annotated[User, Depends(SettingsConsentGate)]

#: Most tags the preference list may hold; the catalogue has far fewer.
MAX_PREFERRED_TAGS = 12

_QUIET_TIME = re.compile(QUIET_TIME_PATTERN)


def known_exercise_tags() -> list[str]:
    """Every tag in the shipped exercise catalogue, in stable order."""
    seen: dict[str, None] = {}
    for definition in get_catalog().list():
        for tag in definition.tags:
            seen.setdefault(tag, None)
    return list(seen)


class SettingsOut(BaseModel):
    """The planner preferences, plus the tag vocabulary to render them with."""

    preferred_exercise_tags: list[str]
    quiet_hours_enabled: bool
    quiet_start: str
    quiet_end: str
    #: The catalogue's tags, so a client can offer exactly the valid choices.
    known_exercise_tags: list[str]


class SettingsIn(BaseModel):
    """A whole-settings update: every field present, defaults documented."""

    preferred_exercise_tags: list[str] = Field(default_factory=list, max_length=MAX_PREFERRED_TAGS)
    quiet_hours_enabled: bool = False
    quiet_start: str = Field(default="22:00", max_length=5)
    quiet_end: str = Field(default="07:00", max_length=5)

    @field_validator("preferred_exercise_tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        cleaned: dict[str, None] = {}
        for tag in value:
            tag = tag.strip().casefold()
            if tag:
                cleaned.setdefault(tag, None)
        unknown = [tag for tag in cleaned if tag not in known_exercise_tags()]
        if unknown:
            raise ValueError(
                f"unknown exercise tag(s): {', '.join(unknown)}; "
                f"allowed: {', '.join(known_exercise_tags())}"
            )
        return list(cleaned)

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _valid_clock(cls, value: str) -> str:
        if not _QUIET_TIME.match(value):
            raise ValueError("quiet-hour bounds must be 24-hour HH:MM, e.g. 22:00")
        return value


def _out(row: UserSettings | None) -> SettingsOut:
    """Wire shape for a row, or for the defaults when no row exists."""
    if row is None:
        return SettingsOut(
            preferred_exercise_tags=[],
            quiet_hours_enabled=False,
            quiet_start=DEFAULT_QUIET_START.strftime("%H:%M"),
            quiet_end=DEFAULT_QUIET_END.strftime("%H:%M"),
            known_exercise_tags=known_exercise_tags(),
        )
    return SettingsOut(
        preferred_exercise_tags=list(row.preferred_exercise_tags or []),
        quiet_hours_enabled=row.quiet_hours_enabled,
        quiet_start=row.quiet_start,
        quiet_end=row.quiet_end,
        known_exercise_tags=known_exercise_tags(),
    )


@router.get("", response_model=SettingsOut)
async def get_settings(user: SettingsUser, session: SessionDep) -> SettingsOut:
    """The caller's planner preferences (defaults if never saved)."""
    return _out(await SettingsRepository(session).get(user.id))


@router.put("", response_model=SettingsOut)
async def put_settings(payload: SettingsIn, user: SettingsUser, session: SessionDep) -> SettingsOut:
    """Replace the caller's planner preferences."""
    row = await SettingsRepository(session).upsert(
        user_id=user.id,
        preferred_exercise_tags=payload.preferred_exercise_tags,
        quiet_hours_enabled=payload.quiet_hours_enabled,
        quiet_start=payload.quiet_start,
        quiet_end=payload.quiet_end,
    )
    await session.commit()
    await session.refresh(row)
    return _out(row)
