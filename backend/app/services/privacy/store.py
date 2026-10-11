"""Reading the three privacy choices, and enforcing them where they bite.

``privacy_settings`` has one row per person, written on the first change. A
person who never opens Settings → Privacy has **no row**, which is why every
accessor here returns the *default* rather than raising: the defaults are the
current product behaviour, and an absent row must never be read as "the user
said no".

The two booleans are **floors**, not switches that grant anything:

* ``store_chat`` (default ``True``) — permission to save chat turns *when the
  ``store_chat`` consent is also present*. Setting it to ``False`` is a veto:
  saved chat stays off even if the consent is still on file.
* ``store_sentiment`` (default ``True``) — permission to keep derived numeric
  sentiment/emotion scores next to the words. ``False`` forces new writes to
  store none, without touching rows that already exist.

The veto is enforced in the service that owns the write (chat session creation,
the journal route) rather than in a decorator, so it is visible next to the
consent check it strengthens.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.privacy import PrivacySetting

#: Chat may be saved when the person both consented *and* left this on.
DEFAULT_STORE_CHAT = True
#: Derived scores are stored by default; the journal UI has offered a per-entry
#: switch since Day 17, and this is the account-level version of it.
DEFAULT_STORE_SENTIMENT = True


@dataclass(frozen=True)
class PrivacySnapshot:
    """Effective privacy settings for one person (defaults when no row exists)."""

    store_chat: bool
    store_sentiment: bool
    retention_days: int | None
    #: False when the person has never saved a privacy choice.
    persisted: bool

    @property
    def keeps_data_forever(self) -> bool:
        """True when no retention window is set (data lives until deleted)."""
        return self.retention_days is None


DEFAULT_SNAPSHOT = PrivacySnapshot(
    store_chat=DEFAULT_STORE_CHAT,
    store_sentiment=DEFAULT_STORE_SENTIMENT,
    retention_days=None,
    persisted=False,
)


async def load_privacy(session: AsyncSession, user_id: uuid.UUID) -> PrivacySnapshot:
    """The person's effective privacy settings (never raises for a missing row)."""
    row = await session.get(PrivacySetting, user_id)
    if row is None:
        return DEFAULT_SNAPSHOT
    return PrivacySnapshot(
        store_chat=bool(row.store_chat),
        store_sentiment=bool(row.store_sentiment),
        retention_days=row.retention_days,
        persisted=True,
    )


async def chat_storage_allowed(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """May a chat turn be **written to the database** for this person?"""
    return (await load_privacy(session, user_id)).store_chat


async def sentiment_storage_allowed(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """May derived sentiment/emotion scores be written for this person?"""
    return (await load_privacy(session, user_id)).store_sentiment
