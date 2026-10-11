"""Planner preferences (``user_settings``): one row per person, lazily created.

A missing row means "defaults": no exercise preference, quiet hours off,
22:00-07:00 bounds if they are ever switched on. The repository returns
``None`` for a person who has never saved, and the service layer interprets
that as defaults — the database never stores a row nobody wrote.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_settings import UserSettings


class SettingsRepository:
    """Thin data access for :class:`~app.models.user_settings.UserSettings`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> UserSettings | None:
        """The row for a person, or ``None`` if they have never saved one."""
        return await self._session.get(UserSettings, user_id)

    async def upsert(
        self,
        *,
        user_id: uuid.UUID,
        preferred_exercise_tags: list[str],
        quiet_hours_enabled: bool,
        quiet_start: str,
        quiet_end: str,
    ) -> UserSettings:
        """Create or replace the row. Never commits; the API owns that."""
        row = await self.get(user_id)
        if row is None:
            row = UserSettings(user_id=user_id)
            self._session.add(row)
        row.preferred_exercise_tags = preferred_exercise_tags
        row.quiet_hours_enabled = quiet_hours_enabled
        row.quiet_start = quiet_start
        row.quiet_end = quiet_end
        await self._session.flush()
        return row
