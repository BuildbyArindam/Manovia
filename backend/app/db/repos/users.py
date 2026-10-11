"""User accounts: create, look up, soft-delete, hard-delete, and per-user data keys."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import is_cipher_configured
from app.models.base import utcnow
from app.models.user import User
from app.services.journal.crypto import get_envelope_cipher


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
        """Insert a user and return it with its generated id and wrapped DEK."""
        user_id = uuid.uuid4()
        wrapped_dek: bytes | None = None
        key_id: str | None = None
        if is_cipher_configured():
            try:
                cipher = get_envelope_cipher()
                _dek, wrapped_dek, key_id = cipher.get_or_create_user_key(user_id)
            except Exception:
                # Custom test ciphers without an envelope keyring leave DEK
                # columns unset until an envelope write needs one.
                wrapped_dek = None
                key_id = None

        user = User(
            id=user_id,
            email=email,
            password_hash=password_hash,
            is_anonymous=is_anonymous,
            language=language,
            region=region,
            data_key_encrypted=wrapped_dek,
            data_key_id=key_id,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def ensure_data_key(self, user_id: uuid.UUID) -> tuple[bytes, str]:
        """Return ``(wrapped_dek, key_id)`` for ``user_id``, persisting it on the user row."""
        cipher = get_envelope_cipher()
        user = await self._session.get(User, user_id)
        existing = user.data_key_encrypted if user is not None else None
        _dek, wrapped, kid = cipher.get_or_create_user_key(user_id, existing_wrapped_dek=existing)
        if user is not None and (user.data_key_encrypted != wrapped or user.data_key_id != kid):
            user.data_key_encrypted = wrapped
            user.data_key_id = kid
            await self._session.flush()
        return wrapped, kid

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
        """Stamp ``deleted_at``; ``None`` when there is no such live user."""
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            return None
        user.deleted_at = utcnow()
        await self._session.flush()
        return user

    async def hard_delete(self, user_id: uuid.UUID) -> bool:
        """Delete the user row; every child row goes with it via ON DELETE CASCADE."""
        stmt = delete(User).where(User.id == user_id)
        result = cast("CursorResult[int]", await self._session.execute(stmt))
        return bool(result.rowcount)
