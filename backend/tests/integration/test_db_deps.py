"""The FastAPI data-layer dependencies, wired through a real request.

Day 4 adds the endpoints that use these; the routes below exist only to prove
the plumbing (dependency → session → repository → commit) works before there is
a public API that depends on it.
"""

from __future__ import annotations

from typing import Annotated

import pytest_asyncio
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_database, get_session
from app.db.repos import UserRepository
from app.db.session import Database
from app.models import User

# Annotated dependencies, the form Day 4's endpoints will use.
SessionDep = Annotated[AsyncSession, Depends(get_session)]
DatabaseDep = Annotated[Database, Depends(get_database)]


def _probe_router() -> APIRouter:
    router = APIRouter()

    @router.post("/users")
    async def create_user(session: SessionDep) -> dict[str, str]:
        user = await UserRepository(session).create(email="via-depends@example.test")
        await session.commit()
        return {"id": str(user.id)}

    @router.get("/users/count")
    async def count_users(session: SessionDep) -> dict[str, int]:
        total = await session.execute(select(func.count()).select_from(User))
        return {"count": int(total.scalar_one())}

    @router.get("/db-backend")
    async def db_backend(database: DatabaseDep) -> dict[str, str]:
        return {"backend": database.engine.name}

    return router


@pytest_asyncio.fixture
async def probe_app(app: FastAPI) -> FastAPI:
    app.include_router(_probe_router(), prefix="/api/v1")
    return app


async def _client(probe_app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=probe_app), base_url="http://testserver")


async def test_session_dependency_writes_and_reads_back(
    probe_app: FastAPI, database: Database
) -> None:
    async with await _client(probe_app) as ac:
        created = await ac.post("/api/v1/users")
        assert created.status_code == 200
        assert probe_app.state.db is database
        assert (await ac.get("/api/v1/users/count")).json() == {"count": 1}


async def test_database_dependency_exposes_the_engine(probe_app: FastAPI) -> None:
    async with await _client(probe_app) as ac:
        response = await ac.get("/api/v1/db-backend")
    assert response.status_code == 200
    assert response.json() == {"backend": "sqlite"}


async def test_a_route_that_does_not_commit_loses_its_write(probe_app: FastAPI) -> None:
    """Documents the contract in deps.py: no implicit commit on teardown."""

    router = APIRouter()

    @router.post("/api/v1/uncommitted-user")
    async def uncommitted(session: SessionDep) -> dict[str, str]:
        user = await UserRepository(session).create(email="never-committed@example.test")
        return {"id": str(user.id)}

    probe_app.include_router(router)
    async with await _client(probe_app) as ac:
        assert (await ac.post("/api/v1/uncommitted-user")).status_code == 200
        assert (await ac.get("/api/v1/users/count")).json() == {"count": 0}
