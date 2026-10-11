"""The mood endpoints end to end: bounds, ownership, encryption, safety, streaks.

Everything runs against the real FastAPI app, a real SQLite database and the real
field cipher. Where a test says "no rows" it counts rows in the actual tables,
and where it says "encrypted" it reads the raw column rather than the response.

Three things this file is deliberately paranoid about, because they are the ways
a mood feature goes wrong quietly:

* **A missing tag or an out-of-range mood must be an error, not a correction.**
  Silently clamping valence to 5 or dropping a fourth emotion stores a different
  mood than the one somebody reported.
* **A frightening note must not cost the person their check-in.** The entry is
  saved *and* help is offered; both are asserted, in the same test.
* **The clock is a seam.** ``_now`` is patched so the streak tests pin "today"
  instead of hoping the suite runs at a convenient hour.
"""

from __future__ import annotations

import io
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import structlog
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_rule_engine
from app.core.crypto import decrypt
from app.core.logging import configure_logging
from app.db.repos import MoodRepository, SafetyEventRepository
from app.models.enums import RiskLevel as StoredRiskLevel
from app.models.mood import MoodEntry

MOOD = "/api/v1/mood"
STREAK = "/api/v1/mood/streak"
VERSION = "2026-10-01"

KOLKATA = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")

#: A phrase the shipped rules engine reads as HIGH (the chat fixtures use the same
#: one). Kept in one place so a rule change breaks one line, not five assertions.
HIGH_RISK_NOTE = "I want to kill myself"
ORDINARY_NOTE = "A quiet evening; I read a book and the rain stopped."


@dataclass
class Person:
    """A signed-in guest, with the consents the mood routes need."""

    id: uuid.UUID
    headers: dict[str, str]


async def person(
    client: httpx.AsyncClient,
    *,
    kinds: tuple[str, ...] = ("terms", "privacy"),
) -> Person:
    """Create a guest account and record the agreements a check-in requires.

    ``kinds=()`` leaves the account unconsented, which is how the consent-gate
    test gets a caller who is authenticated but not yet allowed.
    """
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    if kinds:
        granted = await client.post(
            "/api/v1/consent",
            headers=headers,
            json={"grants": [{"kind": k, "version": VERSION, "granted": True} for k in kinds]},
        )
        assert granted.status_code == 201
    return Person(id=uuid.UUID(body["user"]["id"]), headers=headers)


async def seed_entry(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    at: datetime,
    valence: int = 3,
    energy: int = 3,
    emotions: tuple[str, ...] = (),
    factors: tuple[str, ...] = (),
    note: str | None = None,
) -> MoodEntry:
    """Insert a check-in with an explicit timestamp, the way history would look.

    The API always stamps "now"; a range or streak test needs days that are not
    today, so it writes the rows directly and then asks the API about them.
    """
    repo = MoodRepository(session)
    entry = await repo.record(
        user_id=user_id,
        valence=valence,
        energy=energy,
        emotions=list(emotions),
        factors=dict.fromkeys(factors) if factors else None,
        note=note,
    )
    entry.created_at = at
    await session.commit()
    return entry


async def count_moods(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(MoodEntry).where(MoodEntry.user_id == user_id)
    return int((await session.execute(stmt)).scalar_one())


def payload(**overrides: object) -> dict[str, object]:
    """A minimal valid check-in, with an override for whatever a test is about."""
    body: dict[str, object] = {"valence": 3, "energy": 3}
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# Recording a check-in                                                          #
# --------------------------------------------------------------------------- #


async def test_a_check_in_is_recorded_and_comes_back_whole(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)

    response = await client.post(
        MOOD,
        headers=me.headers,
        json=payload(
            valence=4,
            energy=2,
            emotions=["calm", "loneliness"],
            factors=["sleep", "work"],
            note="  Slept badly, but the walk home helped.  ",
            tz="Asia/Kolkata",
        ),
    )

    assert response.status_code == 201
    body = response.json()
    entry = body["entry"]
    assert entry["valence"] == 4
    assert entry["energy"] == 2
    assert entry["emotions"] == ["calm", "loneliness"]
    assert entry["factors"] == ["sleep", "work"]
    assert entry["note"] == "Slept badly, but the walk home helped."
    assert entry["timezone"] == "Asia/Kolkata"
    # "Today" in the caller's zone, derived from the timestamp we were given.
    assert entry["local_date"] == (
        datetime.fromisoformat(entry["created_at"]).astimezone(KOLKATA).date().isoformat()
    )
    assert uuid.UUID(entry["id"]) == (await MoodRepository(session).list_for_user(me.id))[0].id
    assert body["crisis"] is None
    assert body["streak"]["current_streak"] == 1
    assert body["streak"]["state"] == "today"


async def test_two_check_ins_in_a_day_are_two_entries_and_one_streak_day(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_now(monkeypatch, datetime(2026, 3, 15, 12, 0, tzinfo=UTC))
    me = await person(client)

    first = await client.post(
        MOOD, headers=me.headers, json=payload(valence=2, energy=2, tz="Europe/London")
    )
    second = await client.post(
        MOOD, headers=me.headers, json=payload(valence=4, energy=4, tz="Europe/London")
    )

    assert first.status_code == second.status_code == 201
    assert second.json()["streak"]["current_streak"] == 1
    assert second.json()["streak"]["total_check_ins"] == 2


async def test_auth_and_consent_are_required(client: httpx.AsyncClient) -> None:
    anonymous = await client.post(MOOD, json=payload())
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "token_missing"

    unconsented = await person(client, kinds=())
    refused = await client.post(MOOD, headers=unconsented.headers, json=payload())
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "consent_required"
    assert "terms" in refused.json()["error"]["message"]


# --------------------------------------------------------------------------- #
# Validation bounds — errors, never corrections                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("valence", 0),
        ("valence", 6),
        ("energy", 0),
        ("energy", 6),
    ],
)
async def test_scale_bounds_are_enforced(client: httpx.AsyncClient, field: str, value: int) -> None:
    me = await person(client)

    response = await client.post(MOOD, headers=me.headers, json=payload(**{field: value}))

    assert response.status_code == 422
    # The envelope deliberately carries no field details (they can echo input),
    # so "which field" is asserted on the schema itself in
    # tests/unit/test_mood_request_schema.py.
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("emotions", ["joy", "calm", "sadness", "anger"]),  # one over the cap of 3
        ("factors", ["sleep", "study", "work", "family", "friends", "health"]),  # over 5
    ],
)
async def test_tag_caps_are_enforced(
    client: httpx.AsyncClient, field: str, value: list[str]
) -> None:
    me = await person(client)

    response = await client.post(MOOD, headers=me.headers, json=payload(**{field: value}))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("emotions", ["joy", "confusion"]),
        ("factors", ["work", "moon"]),
    ],
)
async def test_unknown_tags_are_refused(
    client: httpx.AsyncClient, field: str, value: list[str]
) -> None:
    """Anything outside the closed vocabulary is a 422, never a quiet drop.

    The schema-level test (tests/unit/test_mood_request_schema.py) proves the
    developer-facing message names the offending tag and the allowed set; over
    HTTP the envelope stays detail-free by policy.
    """
    me = await person(client)

    response = await client.post(MOOD, headers=me.headers, json=payload(**{field: value}))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_duplicate_tags_are_folded_and_order_is_kept(client: httpx.AsyncClient) -> None:
    me = await person(client)

    response = await client.post(
        MOOD,
        headers=me.headers,
        json=payload(emotions=["Anger", "anger", " calm"], factors=["Work", "work"]),
    )

    assert response.status_code == 201
    assert response.json()["entry"]["emotions"] == ["anger", "calm"]
    assert response.json()["entry"]["factors"] == ["work"]


async def test_a_note_is_bounded_and_blank_means_absent(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)

    too_long = await client.post(MOOD, headers=me.headers, json=payload(note="x" * 1001))
    assert too_long.status_code == 422

    blank = await client.post(MOOD, headers=me.headers, json=payload(note="   \n  "))
    assert blank.status_code == 201
    assert blank.json()["entry"]["note"] is None
    stored = (await MoodRepository(session).list_for_user(me.id))[0]
    assert stored.note_encrypted is None  # nothing was encrypted, because nothing was said


async def test_an_unknown_timezone_is_a_422_not_a_silent_utc(client: httpx.AsyncClient) -> None:
    me = await person(client)

    response = await client.post(MOOD, headers=me.headers, json=payload(tz="Mars/Olympus_Mons"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_timezone"


async def test_tags_that_are_not_strings_are_refused(client: httpx.AsyncClient) -> None:
    me = await person(client)

    response = await client.post(MOOD, headers=me.headers, json=payload(emotions=[1, 2]))

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# The note is private                                                           #
# --------------------------------------------------------------------------- #


async def test_the_note_is_encrypted_at_rest(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    secret = "Therapy starts Thursday and I am frightened about it."

    created = await client.post(MOOD, headers=me.headers, json=payload(note=secret))
    assert created.status_code == 201
    assert created.json()["entry"]["note"] == secret  # the owner may read it back

    stored = (await MoodRepository(session).list_for_user(me.id))[0]
    raw = stored.note_encrypted
    assert raw is not None
    assert secret.encode() not in raw
    assert b"Thursday" not in raw
    assert decrypt(raw) == secret
    # Fernet: the same plaintext twice is never the same ciphertext.
    other = await client.post(MOOD, headers=me.headers, json=payload(note=secret))
    assert other.status_code == 201
    rows = await MoodRepository(session).list_for_user(me.id)
    assert rows[0].note_encrypted != rows[1].note_encrypted


async def test_the_note_never_reaches_the_logs(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    sentinel = "MOOD-NOTE-SENTINEL-4d1f7a"

    buffer = io.StringIO()
    configure_logging()
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(buffer))
    try:
        response = await client.post(MOOD, headers=me.headers, json=payload(note=sentinel))
    finally:
        configure_logging()

    assert response.status_code == 201
    assert sentinel not in buffer.getvalue()
    assert "mood_check_in_recorded" in buffer.getvalue()


async def test_another_person_cannot_read_or_delete_a_check_in(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    owner = await person(client)
    other = await person(client)
    entry = await seed_entry(session, owner.id, at=datetime.now(UTC), note="private")

    # The list is scoped to the caller and never mentions the other row.
    listed = await client.get(MOOD, headers=other.headers, params={"tz": "UTC"})
    assert listed.status_code == 200
    assert listed.json()["count"] == 0

    # Deleting somebody else's row is indistinguishable from deleting a ghost.
    stolen = await client.delete(f"{MOOD}/{entry.id}", headers=other.headers)
    invented = await client.delete(f"{MOOD}/{uuid.uuid4()}", headers=other.headers)
    assert stolen.status_code == invented.status_code == 404
    assert stolen.json()["error"]["code"] == "mood_entry_not_found"
    assert stolen.json()["error"]["message"] == invented.json()["error"]["message"]
    assert await count_moods(session, owner.id) == 1


async def test_deleting_my_own_check_in_reports_the_streak_that_is_left(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    entry = await seed_entry(
        session, me.id, at=datetime.now(UTC), valence=2, energy=2, factors=("work",)
    )

    deleted = await client.delete(
        f"{MOOD}/{entry.id}", headers=me.headers, params={"tz": "Asia/Kolkata"}
    )

    assert deleted.status_code == 200
    body = deleted.json()
    assert body["deleted"] is True
    assert body["id"] == str(entry.id)
    assert body["remaining_streak"]["current_streak"] == 0
    assert body["remaining_streak"]["state"] == "never"
    assert await count_moods(session, me.id) == 0


# --------------------------------------------------------------------------- #
# Ranges, in the caller's calendar                                              #
# --------------------------------------------------------------------------- #


async def test_a_range_query_reads_whole_local_days(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    # 00:30 on the 10th and 23:30 on the 11th, both in Kolkata.
    before_midnight = datetime(2026, 3, 9, 19, 0, tzinfo=UTC)  # 00:30 IST on 03-10
    after = datetime(2026, 3, 11, 18, 0, tzinfo=UTC)  # 23:30 IST on 03-11
    outside = datetime(2026, 3, 13, 6, 0, tzinfo=UTC)  # 11:30 IST on 03-13
    for moment in (before_midnight, after, outside):
        await seed_entry(session, me.id, at=moment, valence=3, energy=3)

    response = await client.get(
        MOOD,
        headers=me.headers,
        params={"from": "2026-03-10", "to": "2026-03-11", "tz": "Asia/Kolkata"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert [entry["local_date"] for entry in body["entries"]] == ["2026-03-11", "2026-03-10"]
    assert body["timezone"] == "Asia/Kolkata"
    assert body["timezone_fallback_used"] is False
    # The echoed range is the half-open instant range that was actually queried.
    assert body["from"] == "2026-03-09T18:30:00Z"
    assert body["to"] == "2026-03-11T18:30:00Z"


async def test_a_range_query_on_the_23_hour_dst_day_covers_the_whole_day(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """2026-03-08 in New York is 23 hours long; both ends of it are still one day."""
    me = await person(client)
    just_after_local_midnight = datetime(2026, 3, 8, 5, 30, tzinfo=UTC)  # 00:30 EST
    late_same_local_day = datetime(2026, 3, 9, 3, 30, tzinfo=UTC)  # 23:30 EDT on 03-08
    next_local_day = datetime(2026, 3, 9, 4, 30, tzinfo=UTC)  # 00:30 EDT on 03-09
    for moment in (just_after_local_midnight, late_same_local_day, next_local_day):
        await seed_entry(session, me.id, at=moment)

    response = await client.get(
        MOOD,
        headers=me.headers,
        params={"from": "2026-03-08", "to": "2026-03-08", "tz": "America/New_York"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert body["from"] == "2026-03-08T05:00:00Z"  # 00:00 EST
    assert body["to"] == "2026-03-09T04:00:00Z"  # 00:00 EDT — 23 hours later


async def test_a_timestamp_range_is_an_exact_instant_range(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    await seed_entry(session, me.id, at=datetime(2026, 3, 10, 12, 0, tzinfo=UTC))
    await seed_entry(session, me.id, at=datetime(2026, 3, 10, 18, 0, tzinfo=UTC))

    response = await client.get(
        MOOD,
        headers=me.headers,
        params={"from": "2026-03-10T13:00:00Z", "to": "2026-03-10T13:00:00Z", "tz": "UTC"},
    )

    assert response.status_code == 200
    assert response.json()["count"] == 0  # an exact instant with nothing at it


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-03-12", "to": "2026-03-10"},
        {"from": "last tuesday"},
        {"to": "2026-13-45"},
    ],
)
async def test_a_nonsense_range_is_refused(
    client: httpx.AsyncClient, params: dict[str, str]
) -> None:
    me = await person(client)

    response = await client.get(MOOD, headers=me.headers, params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"invalid_range", "validation_error"}


async def test_the_default_list_is_newest_first_and_bounded(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    for day in range(1, 6):
        await seed_entry(session, me.id, at=datetime(2026, 3, day, 9, 0, tzinfo=UTC))

    response = await client.get(MOOD, headers=me.headers, params={"tz": "UTC", "limit": 3})

    body = response.json()
    assert body["count"] == 3
    assert [entry["local_date"] for entry in body["entries"]] == [
        "2026-03-05",
        "2026-03-04",
        "2026-03-03",
    ]


# --------------------------------------------------------------------------- #
# Safety on the note                                                            #
# --------------------------------------------------------------------------- #


async def test_a_frightening_note_gets_help_and_keeps_its_check_in(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)

    response = await client.post(
        MOOD,
        headers=me.headers,
        json=payload(
            valence=1,
            energy=1,
            emotions=["sadness"],
            factors=["health"],
            note=HIGH_RISK_NOTE,
            locale="en",
        ),
    )

    assert response.status_code == 201
    body = response.json()

    # 1. The check-in exists — the whole point of saving first, helping second.
    assert body["entry"]["note"] == HIGH_RISK_NOTE
    assert await count_moods(session, me.id) == 1
    stored = (await MoodRepository(session).list_for_user(me.id))[0]
    assert MoodRepository.note(stored) == HIGH_RISK_NOTE

    # 2. The response carries the crisis metadata, mirroring /crisis/assess.
    crisis = body["crisis"]
    assert crisis is not None
    assert crisis["level"] in {"high", "imminent"}
    assert crisis["stored_level"] == "crisis"
    assert crisis["check_in_saved"] is True
    assert crisis["message"] is not None
    assert crisis["message"]["body"], "a crisis message with no body is not help"
    assert crisis["resources"], "a crisis response with no resources is not help"
    assert crisis["emergency"] is not None
    assert any(
        resource["type"] == "call" and resource["number"] for resource in crisis["resources"]
    )
    assert crisis["source"] in {"rules", "ml", "ensemble.uncertain"}

    # 3. The metadata describes the detection, never the words. The only place the
    #    note appears is the entry's own note field.
    metadata = json.dumps(crisis)
    assert HIGH_RISK_NOTE not in metadata
    # Rationale codes may contain *pattern* words (``si.kill_myself``) — they are a
    # stable vocabulary, not a quotation — but they can never be prose.
    code_re = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
    assert crisis["rationale_codes"]
    assert all(code_re.match(code) for code in crisis["rationale_codes"])

    # 4. Nothing was hidden from the person: they are still told what was recorded.
    assert body["streak"]["total_check_ins"] == 1


async def test_the_safety_event_is_audited_without_any_text(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)

    await client.post(MOOD, headers=me.headers, json=payload(note=HIGH_RISK_NOTE))

    events = await SafetyEventRepository(session).list_for_user(me.id)
    assert len(events) == 1
    event = events[0]
    assert event.risk_level == int(StoredRiskLevel.CRISIS)
    assert event.source.value == "rules"
    assert event.category is None
    assert not hasattr(event, "text")
    # The count-by-tier view agrees, which is what a clinical dashboard reads.
    assert await SafetyEventRepository(session).count_by_risk_level(user_id=me.id) == {
        int(StoredRiskLevel.CRISIS): 1
    }


async def test_an_ordinary_note_gets_no_crisis_card_and_no_audit_row(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)

    response = await client.post(MOOD, headers=me.headers, json=payload(note=ORDINARY_NOTE))

    assert response.status_code == 201
    assert response.json()["crisis"] is None
    assert await count_moods(session, me.id) == 1
    assert list(await SafetyEventRepository(session).list_for_user(me.id)) == []


async def test_a_check_in_without_a_note_never_touches_the_safety_engine(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    """The common case is two integers and some chips: no engine, no cost."""

    class ExplodingEngine:
        def assess(self, text: str, language: str | None = None) -> object:
            raise AssertionError("the rules engine must not run without a note")

    app.dependency_overrides[get_rule_engine] = lambda: ExplodingEngine()
    try:
        me = await person(client)
        response = await client.post(
            MOOD, headers=me.headers, json=payload(emotions=["calm"], factors=["work"])
        )
    finally:
        app.dependency_overrides.pop(get_rule_engine, None)

    assert response.status_code == 201
    assert response.json()["crisis"] is None


async def test_when_the_safety_engine_fails_nothing_is_stored(
    client: httpx.AsyncClient, session: AsyncSession, app: FastAPI
) -> None:
    class ExplodingEngine:
        def assess(self, text: str, language: str | None = None) -> object:
            raise RuntimeError("boom")

    me = await person(client)
    app.dependency_overrides[get_rule_engine] = lambda: ExplodingEngine()
    try:
        response = await client.post(MOOD, headers=me.headers, json=payload(note="anything at all"))
    finally:
        app.dependency_overrides.pop(get_rule_engine, None)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "safety_unavailable"
    assert await count_moods(session, me.id) == 0


async def test_a_note_that_reads_medium_is_stored_without_a_crisis_card(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Only HIGH and above produce the card; the brief is explicit about the bar."""
    me = await person(client)

    response = await client.post(
        MOOD,
        headers=me.headers,
        json=payload(note="I feel so hopeless and nothing will ever change"),
    )

    assert response.status_code == 201
    assert response.json()["crisis"] is None
    assert await count_moods(session, me.id) == 1


# --------------------------------------------------------------------------- #
# The streak over HTTP: the clock is pinned, the timezone is the caller's       #
# --------------------------------------------------------------------------- #


def pin_now(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Freeze the API's clock (the seam is ``app.api.v1.mood._now``)."""
    monkeypatch.setattr("app.api.v1.mood._now", lambda: moment)


async def test_streak_counts_in_the_callers_timezone(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_now(monkeypatch, datetime(2026, 3, 11, 12, 0, tzinfo=UTC))
    me = await person(client)
    # 18:29Z on the 10th (23:59 IST) and 18:30Z on the 10th (00:00 IST on the 11th):
    # two consecutive days in Kolkata, one single day in UTC.
    await seed_entry(session, me.id, at=datetime(2026, 3, 10, 18, 29, tzinfo=UTC))
    await seed_entry(session, me.id, at=datetime(2026, 3, 10, 18, 30, tzinfo=UTC))

    kolkata = await client.get(STREAK, headers=me.headers, params={"tz": "Asia/Kolkata"})
    utc = await client.get(STREAK, headers=me.headers, params={"tz": "UTC"})

    assert kolkata.status_code == 200
    assert kolkata.json()["current_streak"] == 2
    assert kolkata.json()["state"] == "today"
    assert kolkata.json()["today"] == "2026-03-11"
    assert kolkata.json()["timezone"] == "Asia/Kolkata"
    # Pinned now is 2026-03-11T12:00Z, so UTC's today is still the 11th — but
    # both instants are on the 10th *UTC*, so only one day is counted.
    assert utc.json()["current_streak"] == 1
    assert utc.json()["today"] == "2026-03-11"
    assert utc.json()["last_check_in"] == "2026-03-10"


async def test_streak_without_a_timezone_is_utc_and_says_so(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_now(monkeypatch, datetime(2026, 3, 11, 12, 0, tzinfo=UTC))
    me = await person(client)

    response = await client.get(STREAK, headers=me.headers)

    assert response.status_code == 200
    assert response.json()["timezone"] == "UTC"
    assert response.json()["timezone_fallback_used"] is True


async def test_streak_is_forgiving_over_http(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = date(2026, 3, 15)
    pin_now(monkeypatch, datetime(2026, 3, 15, 9, 0, tzinfo=UTC))

    me = await person(client)
    bridged = [today - timedelta(days=offset) for offset in (0, 1, 3, 4)]  # the 13th is empty
    for day in bridged:
        await seed_entry(session, me.id, at=datetime(day.year, day.month, day.day, 9, tzinfo=UTC))

    response = await client.get(STREAK, headers=me.headers, params={"tz": "UTC"})

    body = response.json()
    assert body["current_streak"] == 4
    assert body["freezes_used"] == 1
    assert body["freeze_available"] is False
    assert body["longest_streak"] == 4
    assert body["days_since_last_check_in"] == 0


async def test_a_long_gap_returns_you_without_taking_anything_away(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_now(monkeypatch, datetime(2026, 3, 20, 9, 0, tzinfo=UTC))
    me = await person(client)
    for day in (11, 12, 13):
        await seed_entry(session, me.id, at=datetime(2026, 3, day, 9, tzinfo=UTC))

    response = await client.get(STREAK, headers=me.headers, params={"tz": "UTC"})

    body = response.json()
    assert body["state"] == "returning"
    assert body["current_streak"] == 0
    assert body["days_since_last_check_in"] == 7
    assert body["longest_streak"] == 3  # the record survives
    assert body["freeze_available"] is True  # and so does the allowance


async def test_streak_requires_authentication_and_consent(client: httpx.AsyncClient) -> None:
    anonymous = await client.get(STREAK)
    assert anonymous.status_code == 401

    unconsented = await person(client, kinds=())
    refused = await client.get(STREAK, headers=unconsented.headers)
    assert refused.status_code == 403


async def test_streak_rejects_an_unknown_timezone(client: httpx.AsyncClient) -> None:
    me = await person(client)

    response = await client.get(STREAK, headers=me.headers, params={"tz": "Nowhere/Fictional"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_timezone"


async def test_the_streak_is_empty_but_kind_before_the_first_check_in(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_now(monkeypatch, datetime(2026, 3, 15, 9, 0, tzinfo=UTC))
    me = await person(client)

    body = (await client.get(STREAK, headers=me.headers, params={"tz": "Asia/Kolkata"})).json()

    assert body["state"] == "never"
    assert body["current_streak"] == 0
    assert body["longest_streak"] == 0
    assert body["last_check_in"] is None
    assert body["total_check_ins"] == 0


async def test_the_check_in_response_carries_the_timezone_it_used(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_now(monkeypatch, datetime(2026, 3, 15, 9, 0, tzinfo=UTC))
    me = await person(client)

    body = (await client.post(MOOD, headers=me.headers, json=payload(tz="America/New_York"))).json()

    assert body["streak"]["timezone"] == "America/New_York"
    assert body["streak"]["timezone_fallback_used"] is False
    assert body["entry"]["timezone"] == "America/New_York"


async def test_the_timezone_is_reported_as_a_key_not_a_repr(client: httpx.AsyncClient) -> None:
    """Guard rail: a serialised ZoneInfo once looked like ``<ZoneInfo 'UTC'>``."""
    me = await person(client)

    body = (await client.get(STREAK, headers=me.headers, params={"tz": "Europe/London"})).json()

    assert body["timezone"] == "Europe/London"
    assert "<" not in body["timezone"]


async def test_tags_survive_a_round_trip_with_json_columns(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The JSON columns are the storage; the API is the contract. Both agree."""
    me = await person(client)
    await client.post(
        MOOD,
        headers=me.headers,
        json=payload(emotions=["loneliness", "anxiety"], factors=["friends", "weather"]),
    )

    listed = await client.get(MOOD, headers=me.headers, params={"tz": "UTC"})

    entry = listed.json()["entries"][0]
    assert entry["emotions"] == ["loneliness", "anxiety"]
    assert entry["factors"] == ["friends", "weather"]
    statement = select(MoodEntry.emotions, MoodEntry.factors).where(MoodEntry.user_id == me.id)
    raw = (await session.execute(statement)).one()
    assert raw[0] == ["loneliness", "anxiety"]
    # Factors are stored as name -> severity so an intensity can arrive later.
    assert raw[1] == {"friends": 1, "weather": 1}
