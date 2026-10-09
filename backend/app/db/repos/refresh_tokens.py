"""Refresh tokens: issue rows, look up by hash, and revoke families."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken


class RefreshTokenRepository:
    """Thin data access for :class:`~app.models.refresh_token.RefreshToken`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        token_id: uuid.UUID,
        user_id: uuid.UUID,
        family_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> RefreshToken:
        """Store the hash of one newly issued refresh token."""
        token = RefreshToken(
            id=token_id,
            user_id=user_id,
            family_id=family_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self._session.add(token)
        await self._session.flush()
        return token

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Look up a token row by its SHA-256 hash (never by the token itself)."""
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        return (await self._session.execute(stmt)).scalars().one_or_none()

    async def revoke_family(self, family_id: uuid.UUID, *, revoked_at: datetime) -> int:
        """Revoke every token in one rotation family. Returns rows touched."""
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        result = cast("CursorResult[int]", await self._session.execute(stmt))
        await self._session.flush()
        return int(result.rowcount)

    async def revoke_all_for_user(self, user_id: uuid.UUID, *, revoked_at: datetime) -> int:
        """Revoke every active token of one user (sign out everywhere)."""
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        result = cast("CursorResult[int]", await self._session.execute(stmt))
        await self._session.flush()
        return int(result.rowcount)
