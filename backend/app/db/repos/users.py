"""User accounts: create, look up, soft-delete, and hard-delete."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.user import User


class UserRepository:
    """Thin data access for :class:`~app.models.user.User`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        email: str | None = None,
        password_hash: str | None = None,
        is_anonymous: bool = False,
        language: str = "en",
        region: str | None = None,
    ) -> User:
        """Insert a user and return it with its generated id."""
        user = User(
            email=email,
            password_hash=password_hash,
            is_anonymous=is_anonymous,
            language=language,
            region=region,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def get(self, user_id: uuid.UUID) -> User | None:
        """Fetch a user by id, including soft-deleted ones."""
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Fetch a user by exact email match (normalisation belongs to auth)."""
        stmt = select(User).where(User.email == email)
        return (await self._session.execute(stmt)).scalars().one_or_none()

    async def list_active(self, *, limit: int = 50, offset: int = 0) -> Sequence[User]:
        """Most recently created users that have not been soft-deleted."""
        stmt = (
            select(User)
            .where(User.deleted_at.is_(None))
            .order_by(User.created_at.desc(), User.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def soft_delete(self, user_id: uuid.UUID) -> User | None:
        """Stamp ``deleted_at``; ``None`` when there is no such live user.

        Written through the ORM (not a Core ``UPDATE``) so the caller keeps a
        coherent object. The rows themselves stay; a purge deletes them later.
        """
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            return None
        user.deleted_at = utcnow()
        await self._session.flush()
        return user

    async def hard_delete(self, user_id: uuid.UUID) -> bool:
        """Delete the user row; every child row goes with it via ON DELETE CASCADE.

        This is the "erase everything about me" path: it needs no other query,
        and it works because each child table declares ``ondelete="CASCADE"``
        and the relationships set ``passive_deletes=True``.
        """
        stmt = delete(User).where(User.id == user_id)
        result = cast("CursorResult[int]", await self._session.execute(stmt))
        return bool(result.rowcount)
