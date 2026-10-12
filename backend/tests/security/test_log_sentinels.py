"""The Day 22 logging audit: sentinels through the whole product, then a search.

The rule from AGENTS.md is "never log raw message text at INFO+". The only way
to know that holds is to write text that is impossible to confuse with anything
else, push it through every surface that accepts text, and then grep every
place a string can escape to:

* the structured logs (captured here at DEBUG, so nothing is filtered away),
* the exception report of a 500 that happens *while* the text is in flight,
* the HTTP responses themselves,
* the Prometheus exposition (Day 27 added ``/metrics``) — labels must be route
  templates and closed-set categories, so the sweep below includes it.

If a sentinel ever appears, this file fails loudly and the offending call site
is named in the failure message.
"""

from __future__ import annotations

import io
import json
import uuid
from typing import Any

import httpx
import pytest
import structlog

from app.core.logging import configure_logging

#: Distinct per surface, so a failure says *which* path leaked. Deliberately
#: shaped like real impossible-to-collide tokens.
CHAT_SENTINEL = "SENTINEL-CHAT-7f3a91c4-do-not-log"
CHAT_REPLY_SENTINEL = "SENTINEL-CHAT-REPLY-2b8e40"
JOURNAL_TITLE_SENTINEL = "SENTINEL-JOURNAL-TITLE-5c1d"
JOURNAL_BODY_SENTINEL = "SENTINEL-JOURNAL-BODY-88ae2f"
MOOD_NOTE_SENTINEL = "SENTINEL-MOOD-NOTE-1de77b"
CBT_SENTINEL = "SENTINEL-CBT-4a9c02"
EXPORT_SENTINEL = "SENTINEL-EXPORT-6fd3"

SENTINELS = [
    CHAT_SENTINEL,
    CHAT_REPLY_SENTINEL,
    JOURNAL_TITLE_SENTINEL,
    JOURNAL_BODY_SENTINEL,
    MOOD_NOTE_SENTINEL,
    CBT_SENTINEL,
    EXPORT_SENTINEL,
]

CONSENT_VERSION = "2026-10-01"


class _CapturedLogs:
    """Everything structlog writes while the block runs, as one string."""

    def __init__(self) -> None:
        self.buffer = io.StringIO()

    def __enter__(self) -> _CapturedLogs:
        configure_logging("DEBUG")
        structlog.configure(logger_factory=structlog.PrintLoggerFactory(self.buffer))
        return self

    def __exit__(self, *exc_info: object) -> None:
        configure_logging()

    @property
    def text(self) -> str:
        return self.buffer.getvalue()

    @property
    def records(self) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for line in self.text.strip().splitlines():
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                parsed.append({"event": "unparsed", "raw": line})
        return parsed

    def leaked(self, *extra: str) -> list[str]:
        """Sentinels (and any extra strings) found in the captured output."""
        haystack = self.text
        return [needle for needle in (*SENTINELS, *extra) if needle in haystack]


def _report(found: list[str], *, where: str) -> None:
    assert not found, f"{where} leaked: {found}"


async def _signed_in(client: httpx.AsyncClient, kinds: tuple[str, ...]) -> dict[str, str]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    granted = await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": kind, "version": CONSENT_VERSION, "granted": True} for kind in kinds
            ]
        },
    )
    assert granted.status_code == 201, granted.text
    return headers


@pytest.fixture
def wired_chat(app: Any) -> None:
    """The chat surface with a scripted provider, so no network is involved."""
    from app.services.chat import build_orchestrator
    from app.services.llm.fake_provider import FakeLLMProvider

    provider = FakeLLMProvider().script(CHAT_REPLY_SENTINEL)
    app.state.chat_orchestrator = build_orchestrator(app.state.settings, llm=provider)


async def test_the_full_flow_leaks_no_sentinel_into_the_logs(
    client: httpx.AsyncClient, wired_chat: None
) -> None:
    """Chat (persisted and ephemeral), journal, mood, CBT and export, in one run."""
    headers = await _signed_in(client, ("terms", "privacy", "ai_disclosure", "store_chat"))

    # A saved chat session with a terrible message in it.
    opened = await client.post(
        "/api/v1/chat/sessions", headers=headers, json={"save_history": True}
    )
    assert opened.status_code == 201, opened.text
    session_id = opened.json()["id"]

    with _CapturedLogs() as logs:
        sent = await client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            headers=headers,
            json={"message": CHAT_SENTINEL},
        )
        assert sent.status_code in {200, 201}, sent.text
        # The scripted reply is user-visible text too: it must not be logged.
        assert CHAT_REPLY_SENTINEL in sent.text

        journal = await client.post(
            "/api/v1/journal",
            headers=headers,
            json={
                "title": JOURNAL_TITLE_SENTINEL,
                "body": (
                    f"{JOURNAL_BODY_SENTINEL} and some ordinary words so that "
                    "screening has something to read."
                ),
            },
        )
        assert journal.status_code == 201, journal.text

        mood = await client.post(
            "/api/v1/mood",
            headers=headers,
            json={
                "valence": 3,
                "energy": 3,
                "emotions": [],
                "factors": [],
                "note": MOOD_NOTE_SENTINEL,
            },
        )
        assert mood.status_code == 201, mood.text

        cbt = await client.post(
            "/api/v1/cbt/records",
            headers=headers,
            json={
                "situation": CBT_SENTINEL,
                "automatic_thought": CBT_SENTINEL,
                "evidence_for": CBT_SENTINEL,
                "evidence_against": CBT_SENTINEL,
                "balanced_thought": CBT_SENTINEL,
                "emotion": "anxiety",
                "intensity_before": 70,
                "intensity_after": 40,
            },
        )
        assert cbt.status_code == 200, cbt.text

        # The export hands the text back to its owner: the response is allowed
        # to contain it, the log is not.
        exported = await client.get("/api/v1/privacy/export?format=json", headers=headers)
        assert exported.status_code == 200, exported.text
        assert CHAT_SENTINEL in exported.text

    _report(logs.leaked(), where="the structured logs (chat/journal/mood/CBT/export)")
    assert logs.records, "expected log records to have been written"


async def test_an_ephemeral_session_does_not_leak_either(
    client: httpx.AsyncClient, wired_chat: None
) -> None:
    """The un-saved path runs the same pipeline; it must be just as quiet."""
    headers = await _signed_in(client, ("terms", "privacy", "ai_disclosure"))

    opened = await client.post("/api/v1/chat/sessions", headers=headers, json={})
    assert opened.status_code == 201
    session_id = opened.json()["id"]

    with _CapturedLogs() as logs:
        sent = await client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            headers=headers,
            json={"message": CHAT_SENTINEL},
        )
        assert sent.status_code in {200, 201}, sent.text

    _report(logs.leaked(), where="the ephemeral chat path")


async def test_a_crash_while_text_is_in_flight_reports_no_text(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, app: Any
) -> None:
    """The exception report is the easiest place for user text to escape.

    A 500 is forced from *inside* the journal path — after the body has been
    parsed, while the sentinel is in memory — and the traceback structlog
    writes is then searched for it.
    """
    headers = await _signed_in(client, ("terms", "privacy"))
    from app.db.repos.journals import JournalRepository

    async def explode(*args: Any, **kwargs: Any) -> Any:
        """Fail *after* the body was parsed, so the sentinel is in memory."""
        raise RuntimeError("deliberate storage failure")

    monkeypatch.setattr(JournalRepository, "create", explode)

    # httpx re-raises application exceptions by default; this transport lets the
    # 500 come back as a response so the *client-facing* side can be inspected too.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    with _CapturedLogs() as logs:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as crashing:
            response = await crashing.post(
                "/api/v1/journal",
                headers=headers,
                json={"title": JOURNAL_TITLE_SENTINEL, "body": JOURNAL_BODY_SENTINEL},
            )

    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    # The report exists and names the failure — without the person's words.
    assert "unhandled_exception" in logs.text or "deliberate failure" in logs.text
    _report(logs.leaked(), where="the 500 exception report")
    assert JOURNAL_BODY_SENTINEL not in response.text

    # The failure is still *findable*: the request id in the response is the
    # request id in the report, which is what makes a leak-free log useful.
    request_id = response.json()["error"]["request_id"]
    assert request_id in logs.text


async def test_a_rejected_body_does_not_echo_the_text_back(
    client: httpx.AsyncClient,
) -> None:
    """422s are curated: the offending value is never reflected to the caller."""
    headers = await _signed_in(client, ("terms", "privacy"))
    response = await client.post(
        "/api/v1/journal",
        headers=headers,
        json={"title": JOURNAL_TITLE_SENTINEL * 40, "body": JOURNAL_BODY_SENTINEL},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] in {"validation_error", "invalid_input"}
    assert JOURNAL_TITLE_SENTINEL not in response.text
    assert JOURNAL_BODY_SENTINEL not in response.text


async def test_sentinels_in_query_parameters_never_reach_the_request_log(
    client: httpx.AsyncClient,
) -> None:
    """The request line is logged as a *path*, never a query string.

    Query strings are a classic leak: they end up in access logs, proxy logs and
    error reports. This asserts the middleware logs the path only.
    """
    headers = await _signed_in(client, ("terms", "privacy"))

    with _CapturedLogs() as logs:
        response = await client.get(
            "/api/v1/insights/mood/trends",
            headers=headers,
            params={"tz": EXPORT_SENTINEL, "locale": EXPORT_SENTINEL},
        )
        # Whatever the status, the request was logged and the value was seen.
        assert response.status_code < 500, response.text

    request_records = [record for record in logs.records if record.get("event") == "http_request"]
    assert request_records, "expected http_request records"
    assert all("?" not in str(record.get("path", "")) for record in request_records)
    _report(logs.leaked(), where="the query-parameter path")

    with _CapturedLogs() as logs:
        refused = await client.get(
            "/api/v1/privacy/export",
            headers=headers,
            params={"format": EXPORT_SENTINEL},
        )
        assert refused.status_code == 422, refused.text
    assert EXPORT_SENTINEL not in refused.text
    _report(logs.leaked(EXPORT_SENTINEL), where="a rejected query parameter")


async def test_authentication_failures_do_not_log_the_presented_token(
    client: httpx.AsyncClient,
) -> None:
    """A wrong token is still a secret-shaped string: it must not be logged."""
    presented = f"SENTINEL-TOKEN-{uuid.uuid4().hex}"

    with _CapturedLogs() as logs:
        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {presented}"}
        )
        assert response.status_code == 401

    assert presented not in logs.text, "the presented bearer token was logged"
    assert presented not in response.text


async def test_every_surface_is_still_logged_so_the_audit_is_not_vacuous(
    client: httpx.AsyncClient, wired_chat: None
) -> None:
    """A quiet log is only good news if the log is actually being written."""
    headers = await _signed_in(client, ("terms", "privacy", "ai_disclosure"))
    opened = await client.post("/api/v1/chat/sessions", headers=headers, json={})

    with _CapturedLogs() as logs:
        await client.post(
            f"/api/v1/chat/sessions/{opened.json()['id']}/messages",
            headers=headers,
            json={"message": CHAT_SENTINEL},
        )
        await client.post(
            "/api/v1/journal",
            headers=headers,
            json={"body": JOURNAL_BODY_SENTINEL},
        )

    events = {record.get("event") for record in logs.records}
    assert "http_request" in events
    # At DEBUG the pipeline narrates itself; none of those lines carry the text.
    assert len(logs.records) > 2
    _report(logs.leaked(), where="the narrating pipeline")


async def test_the_prometheus_exposition_carries_no_sentinel(
    client: httpx.AsyncClient, wired_chat: None
) -> None:
    """Day 27 added /metrics. Labels are route templates and closed enums —
    a sentinel pushed through the chat surface must not appear in it, not even
    as a path the person typed (a 404 with the sentinel in the path included).
    """
    from app.core.metrics import reset_metrics

    reset_metrics()
    headers = await _signed_in(client, ("terms", "privacy", "ai_disclosure"))
    opened = await client.post("/api/v1/chat/sessions", headers=headers, json={})
    assert opened.status_code == 201
    session_id = opened.json()["id"]

    sent = await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=headers,
        json={"message": CHAT_SENTINEL},
    )
    assert sent.status_code in {200, 201}, sent.text
    # A path that carries the sentinel must collapse into the fixed label.
    await client.get(f"/definitely-not-a-route/{CHAT_SENTINEL}")

    exposition = (await client.get("/metrics")).text
    assert exposition, "expected the exposition to have content"
    for needle in (*SENTINELS, session_id):
        assert needle not in exposition
    reset_metrics()
