"""Day 25: check-ins queued offline and synced later — idempotent and correctly dated.

A check-in made with no connection waits on the device and is sent when the
connection returns, with two extra fields:

* ``client_id`` — a random UUID that makes the send idempotent. The response to
  the first attempt can be lost (the phone drops off the network after the
  server has written the row), so the client *will* retry, and two open tabs can
  sync the same queue at the same moment. Each case must store exactly one row.
* ``recorded_at`` — when the person actually checked in, so a Tuesday-morning
  check-in synced on Wednesday still counts for Tuesday. Bounded: a week back at
  most, and only a few minutes forward (a fast phone clock).

Everything runs against the real app, a real SQLite database and the real safety
engine. Row counts are read from the table, not inferred from responses.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import SafetyEventRepository
from app.models.mood import MoodEntry

MOOD = "/api/v1/mood"
VERSION = "2026-10-01"
KOLKATA = ZoneInfo("Asia/Kolkata")
HIGH_RISK_NOTE = "I want to kill myself"


async def guest(client: httpx.AsyncClient) -> tuple[uuid.UUID, dict[str, str]]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    granted = await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": kind, "version": VERSION, "granted": True} for kind in ("terms", "privacy")
            ]
        },
    )
    assert granted.status_code == 201
    return uuid.UUID(body["user"]["id"]), headers


async def rows_for(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(MoodEntry).where(MoodEntry.user_id == user_id)
    return int((await session.execute(stmt)).scalar_one())


def pin_now(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    monkeypatch.setattr("app.api.v1.mood._now", lambda: moment)


# --------------------------------------------------------------------------- #
# Idempotency                                                                   #
# --------------------------------------------------------------------------- #


async def test_the_same_client_id_twice_stores_one_check_in(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me, headers = await guest(client)
    client_id = str(uuid.uuid4())
    body = {"valence": 4, "energy": 2, "emotions": ["calm"], "client_id": client_id}

    first = await client.post(MOOD, headers=headers, json=body)
    second = await client.post(MOOD, headers=headers, json=body)

    assert first.status_code == 201
    assert first.json()["replayed"] is False
    # The retry is answered, not refused: the client only needs to know it is saved.
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["entry"]["id"] == first.json()["entry"]["id"]
    assert await rows_for(session, me) == 1
    assert second.json()["streak"]["total_check_ins"] == 1


async def test_first_write_wins_when_a_retry_differs(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """A retry is a retry: changing fields under the same key changes nothing."""
    me, headers = await guest(client)
    client_id = str(uuid.uuid4())

    await client.post(
        MOOD, headers=headers, json={"valence": 2, "energy": 2, "client_id": client_id}
    )
    replay = await client.post(
        MOOD, headers=headers, json={"valence": 5, "energy": 5, "client_id": client_id}
    )

    assert replay.status_code == 200
    assert replay.json()["entry"]["valence"] == 2
    assert await rows_for(session, me) == 1


async def test_a_client_id_is_only_unique_per_person(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Somebody else's key is not a replay — and never leaks their check-in."""
    alice, alice_headers = await guest(client)
    bob, bob_headers = await guest(client)
    shared = str(uuid.uuid4())

    a = await client.post(
        MOOD, headers=alice_headers, json={"valence": 1, "energy": 1, "client_id": shared}
    )
    b = await client.post(
        MOOD, headers=bob_headers, json={"valence": 5, "energy": 5, "client_id": shared}
    )

    assert a.status_code == 201
    assert b.status_code == 201
    assert b.json()["replayed"] is False
    assert b.json()["entry"]["id"] != a.json()["entry"]["id"]
    assert b.json()["entry"]["valence"] == 5
    assert await rows_for(session, alice) == 1
    assert await rows_for(session, bob) == 1


async def test_without_a_client_id_two_posts_are_two_check_ins(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Online check-ins from older clients behave exactly as before."""
    me, headers = await guest(client)

    for _ in range(2):
        response = await client.post(MOOD, headers=headers, json={"valence": 3, "energy": 3})
        assert response.status_code == 201
        assert response.json()["replayed"] is False

    assert await rows_for(session, me) == 2


async def test_concurrent_syncs_of_one_check_in_store_it_once(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Two tabs syncing the same queue at once: the unique index decides."""
    me, headers = await guest(client)
    body = {"valence": 3, "energy": 4, "client_id": str(uuid.uuid4())}

    responses = await asyncio.gather(
        *(client.post(MOOD, headers=headers, json=body) for _ in range(4))
    )

    assert sorted(r.status_code for r in responses).count(201) == 1
    assert all(r.status_code in {200, 201} for r in responses)
    assert len({r.json()["entry"]["id"] for r in responses}) == 1
    assert await rows_for(session, me) == 1


async def test_a_replayed_frightening_note_still_gets_help_but_one_audit_row(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The first answer — the one with the helplines — may be the one that was lost."""
    me, headers = await guest(client)
    body = {"valence": 1, "energy": 1, "note": HIGH_RISK_NOTE, "client_id": str(uuid.uuid4())}

    first = await client.post(MOOD, headers=headers, json=body)
    replay = await client.post(MOOD, headers=headers, json=body)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["crisis"] is not None
    assert replay.json()["crisis"]["resources"], "a replay must still carry the helplines"
    assert await rows_for(session, me) == 1
    # One check-in, one audit row: the replay re-reads, it does not re-record.
    assert len(list(await SafetyEventRepository(session).list_for_user(me))) == 1


async def test_a_malformed_client_id_is_a_422(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)

    response = await client.post(
        MOOD, headers=headers, json={"valence": 3, "energy": 3, "client_id": "not-a-uuid"}
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# recorded_at: dated when it happened, inside a bounded window                  #
# --------------------------------------------------------------------------- #


async def test_an_offline_check_in_counts_for_the_day_it_was_made(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    me, headers = await guest(client)
    now = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)  # 11:30 in Kolkata, Wednesday
    pin_now(monkeypatch, now)
    made = datetime(2026, 10, 13, 9, 15, tzinfo=KOLKATA)  # Tuesday morning, offline

    response = await client.post(
        MOOD,
        headers=headers,
        json={
            "valence": 4,
            "energy": 3,
            "tz": "Asia/Kolkata",
            "client_id": str(uuid.uuid4()),
            "recorded_at": made.isoformat(),
        },
    )

    assert response.status_code == 201
    entry = response.json()["entry"]
    assert entry["local_date"] == "2026-10-13"
    assert datetime.fromisoformat(entry["created_at"]) == made.astimezone(UTC)
    streak = response.json()["streak"]
    assert streak["last_check_in"] == "2026-10-13"
    assert streak["checked_in_today"] is False
    stored = (await session.execute(select(MoodEntry).where(MoodEntry.user_id == me))).scalar_one()
    assert stored.created_at.replace(tzinfo=UTC) == made.astimezone(UTC)


async def test_a_slightly_fast_device_clock_is_clamped_to_now(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, headers = await guest(client)
    now = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
    pin_now(monkeypatch, now)

    response = await client.post(
        MOOD,
        headers=headers,
        json={
            "valence": 3,
            "energy": 3,
            "recorded_at": (now + timedelta(minutes=4)).isoformat(),
        },
    )

    assert response.status_code == 201
    assert datetime.fromisoformat(response.json()["entry"]["created_at"]) == now


@pytest.mark.parametrize(
    ("offset", "fragment"),
    [
        (timedelta(minutes=30), "in the future"),
        (-timedelta(days=7, minutes=1), "more than 7 days old"),
    ],
)
async def test_a_time_outside_the_window_is_refused_not_moved(
    client: httpx.AsyncClient,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    offset: timedelta,
    fragment: str,
) -> None:
    me, headers = await guest(client)
    now = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
    pin_now(monkeypatch, now)

    response = await client.post(
        MOOD,
        headers=headers,
        json={"valence": 3, "energy": 3, "recorded_at": (now + offset).isoformat()},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_recorded_at"
    assert fragment in response.json()["error"]["message"]
    assert await rows_for(session, me) == 0


async def test_exactly_seven_days_back_is_still_accepted(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, headers = await guest(client)
    now = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
    pin_now(monkeypatch, now)

    response = await client.post(
        MOOD,
        headers=headers,
        json={"valence": 3, "energy": 3, "recorded_at": (now - timedelta(days=7)).isoformat()},
    )

    assert response.status_code == 201


async def test_a_naive_recorded_at_is_refused(client: httpx.AsyncClient) -> None:
    """Without an offset the instant is ambiguous by up to a day; refuse it."""
    _, headers = await guest(client)

    response = await client.post(
        MOOD,
        headers=headers,
        json={"valence": 3, "energy": 3, "recorded_at": "2026-10-13T09:15:00"},
    )

    assert response.status_code == 422


async def test_the_race_branch_reads_the_winner_after_the_unique_index_fires(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force the interleaving the ASGI test transport cannot produce on its own.

    The second request's "already stored?" read happens *before* the first
    request commits (simulated by hiding the row from that one read), so the
    insert reaches the unique index, which raises; the handler must roll back and
    answer with the winner — and the loser's safety event must not survive.
    """
    from app.db.repos.moods import MoodRepository

    me, headers = await guest(client)
    body = {"valence": 2, "energy": 2, "note": HIGH_RISK_NOTE, "client_id": str(uuid.uuid4())}
    first = await client.post(MOOD, headers=headers, json=body)
    assert first.status_code == 201

    real = MoodRepository.get_by_client_id
    calls = {"n": 0}

    async def stale_first_read(
        self: MoodRepository, user_id: uuid.UUID, client_id: uuid.UUID
    ) -> MoodEntry | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # the read that lost the race
        return await real(self, user_id, client_id)

    monkeypatch.setattr(MoodRepository, "get_by_client_id", stale_first_read)
    second = await client.post(MOOD, headers=headers, json=body)

    assert calls["n"] == 2, "the handler must re-read after the unique index fired"
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["entry"]["id"] == first.json()["entry"]["id"]
    assert second.json()["crisis"] is not None
    assert await rows_for(session, me) == 1
    assert len(list(await SafetyEventRepository(session).list_for_user(me))) == 1
