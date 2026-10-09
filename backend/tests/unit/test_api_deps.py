"""The FastAPI dependency accessors in app.api.deps (Day 4)."""

from __future__ import annotations

from typing import cast

import pytest
from fastapi import Request

from app.api.deps import (
    get_consent_documents,
    get_database,
    get_login_lockout,
    get_token_service,
    require_consent,
)
from app.core.lockout import LoginLockout
from app.core.tokens import TokenService
from app.db.session import Database
from app.models.enums import ConsentKind


class _State:
    def __init__(self) -> None:
        self.db = cast("Database", object())
        self.token_service = cast("TokenService", object())
        self.login_lockout = cast("LoginLockout", object())


def _request() -> Request:
    state = _State()
    app = type("App", (), {"state": state})()
    return cast("Request", type("Request", (), {"app": app})())


def test_get_database_reads_app_state() -> None:
    request = _request()
    assert get_database(request) is request.app.state.db


def test_get_token_service_reads_app_state() -> None:
    request = _request()
    assert get_token_service(request) is request.app.state.token_service


def test_get_login_lockout_reads_app_state() -> None:
    request = _request()
    assert get_login_lockout(request) is request.app.state.login_lockout


def test_get_consent_documents_returns_the_shipped_set() -> None:
    documents = get_consent_documents()
    assert [document.kind for document in documents.all()] == list(ConsentKind)


def test_require_consent_without_kinds_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="at least one"):
        require_consent()
