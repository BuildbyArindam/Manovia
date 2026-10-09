"""FastAPI dependencies for the data layer.

``get_session`` hands out a session but does not commit: a request that writes
must call ``await session.commit()`` itself (or use ``session.begin()`` for
explicit transaction scope). Keeping commits out of the dependency makes the
write boundary visible in the endpoint instead of hidden in teardown code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import Database


def get_database(request: Request) -> Database:
    """The engine/session factory owned by this application instance."""
    database: Database = request.app.state.db
    return database


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A per-request :class:`AsyncSession`."""
    database: Database = request.app.state.db
    async with database.session_factory() as session:
        yield session
