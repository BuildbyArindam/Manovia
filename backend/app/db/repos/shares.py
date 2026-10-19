"""Data access for the clinician-share bridge (Day 47).

One rule shapes this module: **the budget is claimed in the database, not in
Python.** :meth:`ShareRepository.claim_view` is a single ``UPDATE … WHERE
view_count < view_limit`` whose rowcount decides whether a viewer gets the
ciphertext. Two simultaneous opens of a one-view link therefore hand out one
document, not two — a check-then-increment in application code would hand out
both, and the only way to notice is to be the person whose "view once" link was
viewed twice.

Nothing here commits; the caller owns the transaction, as everywhere else in
:mod:`app.db.repos`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.enums import ShareEventKind
from app.models.share import ClinicianShare, ShareAccessEvent, SharePreference
from app.services.sharing.crypto import hash_token

#: Who an audit line happened to. Closed set, stored as a short string.
ACTOR_OWNER = "owner"
ACTOR_VIEWER = "viewer"
ACTOR_SYSTEM = "system"


class ShareRepository:
    """Thin data access for shares, their audit log and the per-person switch."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- the switch --------------------------------------------------------
    async def preference(self, user_id: uuid.UUID) -> SharePreference | None:
        """The person's sharing switch, or ``None`` when they never touched it."""

        return await self._session.get(SharePreference, user_id)

    async def set_preference(
        self, user_id: uuid.UUID, *, enabled: bool, now: datetime | None = None
    ) -> SharePreference:
        """Turn sharing on or off. Creates the row the first time."""

        moment = now or utcnow()
        row = await self._session.get(SharePreference, user_id)
        if row is None:
            row = SharePreference(
                user_id=user_id, enabled=enabled, created_at=moment, updated_at=moment
            )
            self._session.add(row)
        else:
            row.enabled = enabled
            row.updated_at = moment
        await self._session.flush()
        return row

    # -- shares ------------------------------------------------------------
    async def add(self, share: ClinicianShare) -> ClinicianShare:
        """Persist a new share row."""

        self._session.add(share)
        await self._session.flush()
        return share

    async def get(self, share_id: uuid.UUID) -> ClinicianShare | None:
        """One share by its internal id (the owner's own list uses these)."""

        return await self._session.get(ClinicianShare, share_id)

    async def get_by_token(self, token: str) -> ClinicianShare | None:
        """One share by public token. The token itself is never stored."""

        statement = select(ClinicianShare).where(ClinicianShare.token_hash == hash_token(token))
        return (await self._session.execute(statement)).scalars().first()

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[ClinicianShare]:
        """Every share a person has created, newest first."""

        statement = (
            select(ClinicianShare)
            .where(ClinicianShare.user_id == user_id)
            .order_by(ClinicianShare.created_at.desc(), ClinicianShare.id.desc())
        )
        return (await self._session.execute(statement)).scalars().all()

    async def active_for_user(self, user_id: uuid.UUID) -> Sequence[ClinicianShare]:
        """Shares that are neither revoked nor past their expiry."""

        now = utcnow()
        statement = (
            select(ClinicianShare)
            .where(
                ClinicianShare.user_id == user_id,
                ClinicianShare.revoked_at.is_(None),
                ClinicianShare.expires_at > now,
            )
            .order_by(ClinicianShare.created_at.desc(), ClinicianShare.id.desc())
        )
        return (await self._session.execute(statement)).scalars().all()

    async def count_active(self, user_id: uuid.UUID) -> int:
        """How many live shares this person has (the per-person ceiling check)."""

        now = utcnow()
        statement = (
            select(func.count())
            .select_from(ClinicianShare)
            .where(
                ClinicianShare.user_id == user_id,
                ClinicianShare.revoked_at.is_(None),
                ClinicianShare.expires_at > now,
            )
        )
        return int((await self._session.execute(statement)).scalar() or 0)

    async def claim_view(self, share_id: uuid.UUID) -> bool:
        """Atomically take one view from the budget. ``True`` when it was taken.

        One statement, so the check and the increment cannot be separated by
        another request.
        """

        statement = (
            update(ClinicianShare)
            .where(
                ClinicianShare.id == share_id,
                ClinicianShare.view_count < ClinicianShare.view_limit,
            )
            .values(view_count=ClinicianShare.view_count + 1)
        )
        result = await self._session.execute(statement)
        return int(getattr(result, "rowcount", 0) or 0) == 1

    async def register_passcode_failure(
        self, share: ClinicianShare, *, lock: bool, locked_until: datetime | None
    ) -> int:
        """Count a wrong passcode; optionally lock the link. Returns the count.

        A lock resets the counter: the punishment is the wait, not an
        ever-growing number that would make the lockout permanent by accident.
        """

        if lock:
            share.passcode_failures = 0
            share.locked_until = locked_until
        else:
            share.passcode_failures = int(share.passcode_failures) + 1
            share.locked_until = None
        await self._session.flush()
        return int(share.passcode_failures)

    async def clear_passcode_failures(self, share: ClinicianShare) -> None:
        """A successful open forgets the earlier misses."""

        share.passcode_failures = 0
        share.locked_until = None
        await self._session.flush()

    async def revoke(self, share: ClinicianShare, *, now: datetime | None = None) -> ClinicianShare:
        """Stamp the revocation. Idempotent: revoking twice changes nothing."""

        if share.revoked_at is None:
            share.revoked_at = now or utcnow()
            await self._session.flush()
        return share

    async def revoke_all(self, user_id: uuid.UUID, *, now: datetime | None = None) -> list[uuid.UUID]:
        """Revoke every live share at once and return the ids that changed."""

        moment = now or utcnow()
        live = await self.active_for_user(user_id)
        ids = [share.id for share in live]
        if not ids:
            return []
        statement = (
            update(ClinicianShare)
            .where(ClinicianShare.id.in_(ids), ClinicianShare.revoked_at.is_(None))
            .values(revoked_at=moment)
        )
        await self._session.execute(statement)
        await self._session.flush()
        return ids

    # -- audit log ---------------------------------------------------------
    async def log(
        self,
        share_id: uuid.UUID,
        kind: ShareEventKind,
        *,
        actor: str = ACTOR_SYSTEM,
        now: datetime | None = None,
    ) -> ShareAccessEvent:
        """Append one audit line. No free text, no address, no agent string."""

        event = ShareAccessEvent(share_id=share_id, kind=kind, actor=actor, created_at=now or utcnow())
        self._session.add(event)
        await self._session.flush()
        return event

    async def events(self, share_id: uuid.UUID) -> Sequence[ShareAccessEvent]:
        """The whole log for one share, oldest first."""

        statement = (
            select(ShareAccessEvent)
            .where(ShareAccessEvent.share_id == share_id)
            .order_by(ShareAccessEvent.created_at, ShareAccessEvent.id)
        )
        return (await self._session.execute(statement)).scalars().all()

    async def event_counts(self, share_id: uuid.UUID) -> dict[str, int]:
        """How many of each kind — the numbers behind "opened 2 times"."""

        statement = (
            select(ShareAccessEvent.kind, func.count())
            .where(ShareAccessEvent.share_id == share_id)
            .group_by(ShareAccessEvent.kind)
        )
        rows = (await self._session.execute(statement)).all()
        # ``ShareEventKind`` is a StrEnum, so ``str()`` is the stored value.
        return {str(kind): int(count) for kind, count in rows}

    async def delete(self, share: ClinicianShare) -> None:
        """Remove one share row (used by the privacy tests and by retention)."""

        await self._session.delete(share)
        await self._session.flush()


__all__ = [
    "ACTOR_OWNER",
    "ACTOR_SYSTEM",
    "ACTOR_VIEWER",
    "ShareRepository",
]
