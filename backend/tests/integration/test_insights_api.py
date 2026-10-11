"""The insights endpoints end to end: the two routes, their data rules, their wording.

Everything runs against the real FastAPI app, a real SQLite database and the real
field cipher. The synthetic data is generated to have *known answers*, so the
assertions are about arithmetic rather than about a response shape:

* a 60-day series where ``sleep`` lowers mood by about a point and ``friends``
  raises it by about half a point — the app must find both;
* three check-ins — the app must claim nothing;
* a pure-noise series — the app must not invent a strong association.

Four promises this file exists to keep honest, because each one is a way this
feature fails quietly:

* **No claim without data.** Below the minimums the response says so in words.
* **No causal language.** Not in a note, not in the weekly summary, not anywhere.
* **The person's own notes are not an input.** The statistics read scales, tags
  and timestamps; a note is never part of a pattern.
* **Another account's data is invisible.**
"""

from __future__ import annotations

import random
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import MoodRepository

WEEKLY = "/api/v1/insights/weekly"
PATTERNS = "/api/v1/insights/patterns"
EXPORT = "/api/v1/insights/export"
VERSION = "2026-10-01"

KOLKATA = ZoneInfo("Asia/Kolkata")

#: The instant every test treats as "now". Insights are computed relative to the
#: clock, so it is pinned: a test that seeded a fixed calendar window would
#: otherwise be asking about a different 90 days than the one it wrote.
NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)

#: The last day of every seeded series — the day before :data:`NOW`.
END = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)

#: The words that must never appear in anything this feature generates.
CAUSAL_PHRASES = (
    "causes",
    "caused",
    "because of",
    "due to",
    "leads to",
    "results in",
    "makes you",
    "is the reason",
)


@dataclass
class Person:
    """A signed-in guest with the agreements the insights routes need."""

    id: uuid.UUID
    headers: dict[str, str]


async def person(
    client: httpx.AsyncClient, *, kinds: tuple[str, ...] = ("terms", "privacy")
) -> Person:
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


async def seed(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    at: datetime,
    valence: int = 3,
    energy: int = 3,
    factors: tuple[str, ...] = (),
    emotions: tuple[str, ...] = (),
    note: str | None = None,
) -> None:
    """Write one check-in at an explicit instant (the API would stamp \"now\")."""
    entry = await MoodRepository(session).record(
        user_id=user_id,
        valence=valence,
        energy=energy,
        emotions=list(emotions),
        factors=dict.fromkeys(factors) if factors else None,
        note=note,
    )
    entry.created_at = at
    await session.commit()


def _series(
    *,
    days: int,
    seed: int,
    base_valence: float = 3.0,
    sleep_effect: float = 0.0,
    friends_effect: float = 0.0,
    sleep_rate: float = 0.3,
    friends_rate: float = 0.25,
    end: datetime,
) -> list[tuple[datetime, int, int, tuple[str, ...]]]:
    """A synthetic series: one check-in a day, with known factor effects.

    Returns ``(instant, valence, energy, factors)`` and is deterministic for a
    seed, so a failure names the exact series that produced it.
    """
    rng = random.Random(seed)
    rows: list[tuple[datetime, int, int, tuple[str, ...]]] = []
    start = end - timedelta(days=days - 1)
    for offset in range(days):
        has_sleep = rng.random() < sleep_rate
        has_friends = rng.random() < friends_rate
        value = base_valence
        if has_sleep:
            value += sleep_effect
        if has_friends:
            value += friends_effect
        # A wide jitter on purpose: a narrow one rounds every day to the same
        # integer, which makes the series constant and every effect size undefined
        # (zero pooled variance) — a degenerate "noise" test that proves nothing.
        valence = max(1, min(5, round(value + rng.uniform(-1.6, 1.6))))
        energy = max(1, min(5, round(3.0 + rng.uniform(-1.6, 1.6))))
        factors: list[str] = []
        if has_sleep:
            factors.append("sleep")
        if has_friends:
            factors.append("friends")
        rows.append(
            (
                (start + timedelta(days=offset)).replace(
                    hour=12, minute=0, second=0, microsecond=0
                ),
                valence,
                energy,
                tuple(factors),
            )
        )
    return rows


async def seed_series(
    session: AsyncSession,
    user_id: uuid.UUID,
    rows: list[tuple[datetime, int, int, tuple[str, ...]]],
) -> None:
    for instant, valence, energy, factors in rows:
        await seed(session, user_id, at=instant, valence=valence, energy=energy, factors=factors)


def pin_now(monkeypatch: pytest.MonkeyPatch, moment: datetime = NOW) -> None:
    """Freeze the API's clock (the seam is ``app.api.v1.insights._now``)."""
    monkeypatch.setattr("app.api.v1.insights._now", lambda: moment)


@pytest.fixture(autouse=True)
def _pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this file runs at :data:`NOW`.

    The endpoints compute their 90-day window and their "this week" from the
    clock, so an unpinned test would be asking about October while it seeded
    March. Pinning once here is why no test below carries a date of its own.
    """
    pin_now(monkeypatch)


def _association(body: Any, factor: str) -> Any:
    """The response row for one factor tag (``Any``: these are decoded JSON)."""
    for association in body["factor_associations"]:
        if association["factor"] == factor:
            return association
    raise AssertionError(f"{factor} missing from the response")


# --------------------------------------------------------------------------- #
# The known-answer series                                                       #
# --------------------------------------------------------------------------- #


async def test_it_finds_the_effects_that_are_really_there(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """60 days; sleep -1.0, friends +0.5. Both must be reported, with sample sizes."""
    end = END
    me = await person(client)
    await seed_series(
        session,
        me.id,
        _series(days=60, seed=42, sleep_effect=-1.0, friends_effect=0.5, end=end),
    )

    response = await client.get(PATTERNS, headers=me.headers, params={"tz": "UTC"})
    assert response.status_code == 200
    body = response.json()

    sleep = _association(body, "sleep")
    friends = _association(body, "friends")

    # Enough data on both sides of each comparison, or the test is vacuous.
    assert sleep["with_factor_count"] >= 5
    assert sleep["without_factor_count"] >= 5
    assert friends["with_factor_count"] >= 5
    assert friends["without_factor_count"] >= 5

    # The sign and roughly the size of each planted effect.
    assert sleep["with_factor_avg"] < sleep["without_factor_avg"]
    assert sleep["effect_size"] < 0
    assert sleep["ci_upper"] < 0  # the whole interval is below zero

    assert friends["with_factor_avg"] > friends["without_factor_avg"]
    assert friends["effect_size"] > 0

    # Every claim carries its sample sizes and an interval.
    assert sleep["ci_lower"] is not None and sleep["ci_upper"] is not None
    assert sleep["p_value"] is not None and 0.0 <= sleep["p_value"] <= 1.0
    assert f"n={sleep['with_factor_count']} vs {sleep['without_factor_count']}" in sleep["note"]


async def test_the_found_effects_read_as_associations(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The wording of a real finding, asserted word for word in spirit."""
    end = END
    me = await person(client)
    await seed_series(
        session, me.id, _series(days=60, seed=42, sleep_effect=-1.0, friends_effect=0.5, end=end)
    )

    body = (await client.get(PATTERNS, headers=me.headers, params={"tz": "UTC"})).json()

    for association in body["factor_associations"]:
        note = association["note"].lower()
        assert "association" in note or "not enough data" in note
        for phrase in CAUSAL_PHRASES:
            assert phrase not in note, f"{phrase!r} in {association['note']!r}"

    sleep_note = _association(body, "sleep")["note"]
    assert "On days you tagged sleep" in sleep_note
    assert "lower on average" in sleep_note
    assert "not a causal link" in sleep_note


# --------------------------------------------------------------------------- #
# Too little data                                                               #
# --------------------------------------------------------------------------- #


async def test_three_check_ins_claim_nothing(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The brief's second check: with three entries the app says "not enough data"."""
    end = END
    me = await person(client)
    await seed(session, me.id, at=end - timedelta(days=2), valence=2, energy=2)
    await seed(session, me.id, at=end - timedelta(days=1), valence=4, energy=3, factors=("sleep",))
    await seed(session, me.id, at=end, valence=3, energy=2, factors=("sleep",))

    body = (await client.get(PATTERNS, headers=me.headers, params={"tz": "UTC"})).json()

    # No trend, in either window, and the response says why.
    assert body["rolling_7"]["trend"] == "not_enough_data"
    assert body["rolling_30"]["trend"] == "not_enough_data"
    assert "at least 7 days" in body["rolling_7"]["trend_note"]

    # No association for sleep: one side of the comparison has fewer than five.
    sleep = _association(body, "sleep")
    assert sleep["effect_size"] is None
    assert sleep["p_value"] is None
    assert sleep["ci_lower"] is None
    assert "Not enough data" in sleep["note"]
    assert "need at least 5" in sleep["note"]

    # Every bucket is below the floor too, and says so rather than showing a mean.
    assert all(pattern["valence_avg"] is None for pattern in body["hourly_patterns"])
    assert all("need 5" in pattern["note"] for pattern in body["hourly_patterns"])

    # The weekly summary says the same thing in a sentence.
    weekly = (await client.get(WEEKLY, headers=me.headers, params={"tz": "UTC"})).json()
    assert weekly["trend"] == "not_enough_data"
    assert "need 7 days" in weekly["text"] or "7 days" in weekly["text"]

    # The data minimums are published, not buried.
    assert body["min_entries_for_trend"] == 7
    assert body["min_entries_per_group"] == 5


async def test_no_check_ins_at_all_is_a_sentence_not_an_error(
    client: httpx.AsyncClient,
) -> None:
    me = await person(client)

    weekly = await client.get(WEEKLY, headers=me.headers)
    assert weekly.status_code == 200
    assert weekly.json()["total_check_ins"] == 0
    assert weekly.json()["valence_avg"] is None
    assert "No check-ins this week" in weekly.json()["text"]

    patterns = await client.get(PATTERNS, headers=me.headers)
    assert patterns.status_code == 200
    body = patterns.json()
    assert body["rolling_7"]["points"] == []
    assert body["rolling_7"]["trend"] == "not_enough_data"
    assert all(a["effect_size"] is None for a in body["factor_associations"])


# --------------------------------------------------------------------------- #
# Noise: no strong pattern should appear                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("noise_seed", range(5))
async def test_pure_noise_produces_no_strong_association(
    client: httpx.AsyncClient,
    session: AsyncSession,
    noise_seed: int,
) -> None:
    """Five independent noise series through the real API.

    The client-side false-positive sweep (20 seeds) runs on the pure functions in
    ``scripts/verify_day16.py``; this is the same guarantee asserted end to end,
    on a handful of seeds, so the HTTP path cannot mask a regression.
    """
    end = END
    me = await person(client)
    # 60 days, no planted effect at all — every tag is assigned at random.
    await seed_series(session, me.id, _series(days=60, seed=9000 + noise_seed, end=end))

    body = (await client.get(PATTERNS, headers=me.headers, params={"tz": "UTC"})).json()

    claimed = [
        association
        for association in body["factor_associations"]
        if association["p_value"] is not None
        and association["p_value"] < 0.05
        and association["effect_size"] is not None
        and abs(association["effect_size"]) > 0.8
    ]
    assert claimed == [], f"noise seed {noise_seed} produced {claimed}"


async def test_a_factor_that_is_never_tagged_is_reported_honestly(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """A tag nobody used is not an effect of zero; it is missing data."""
    end = END
    me = await person(client)
    await seed_series(session, me.id, _series(days=20, seed=7, end=end))

    body = (await client.get(PATTERNS, headers=me.headers, params={"tz": "UTC"})).json()
    weather = _association(body, "weather")
    assert weather["with_factor_count"] == 0
    assert weather["with_factor_avg"] is None
    assert weather["effect_size"] is None
    assert "Not enough data" in weather["note"]


# --------------------------------------------------------------------------- #
# The weekly summary                                                            #
# --------------------------------------------------------------------------- #


async def test_the_weekly_summary_is_localised(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Three locales, three sentences, and the disclaimer in each."""
    me = await person(client)
    await seed_series(session, me.id, _series(days=10, seed=11, end=END))

    english = (
        await client.get(WEEKLY, headers=me.headers, params={"tz": "UTC", "locale": "en"})
    ).json()
    hindi = (
        await client.get(WEEKLY, headers=me.headers, params={"tz": "UTC", "locale": "hi"})
    ).json()
    bengali = (
        await client.get(WEEKLY, headers=me.headers, params={"tz": "UTC", "locale": "bn"})
    ).json()

    assert english["locale"] == "en"
    assert "Your week in review" in english["text"]
    assert hindi["text"] != english["text"]
    assert any("\u0900" <= ch <= "\u097f" for ch in hindi["text"]), "hi text is not Devanagari"
    assert any("\u0980" <= ch <= "\u09ff" for ch in bengali["text"]), "bn text is not Bengali"

    # The limitation travels with the summary, in every language: a summary that
    # reads like a verdict is worse than no summary.
    for body in (english, hindi, bengali):
        assert (
            "diagnosis" in body["text"].lower()
            or "निदान" in body["text"]
            or "নির্ণয়" in body["text"]
        )


async def test_an_unknown_locale_is_refused_by_the_schema(client: httpx.AsyncClient) -> None:
    me = await person(client)
    response = await client.get(WEEKLY, headers=me.headers, params={"locale": "fr"})
    assert response.status_code == 422


async def test_the_weekly_summary_never_claims_a_cause(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    end = END
    me = await person(client)
    await seed_series(
        session, me.id, _series(days=30, seed=3, sleep_effect=-1.0, friends_effect=0.5, end=end)
    )

    for locale in ("en", "hi", "bn"):
        text = (
            await client.get(WEEKLY, headers=me.headers, params={"tz": "UTC", "locale": locale})
        ).json()["text"]
        lowered = text.lower()
        for phrase in CAUSAL_PHRASES:
            assert phrase not in lowered, f"{phrase!r} in the {locale} summary"


# --------------------------------------------------------------------------- #
# Privacy and ownership                                                         #
# --------------------------------------------------------------------------- #


async def test_a_note_is_never_part_of_a_pattern(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The statistics read scales, tags and timestamps — never the note text.

    Asserted with a canary whose words are also a factor name and a causal verb:
    if a note were ever fed into the statistics, the canary's own text would show
    up in a note-and-summary field, or its words would move a number. The export
    is the one endpoint that legitimately carries the note, and it is asserted
    from the other side — the canary is in the entries bundle and *only* there.
    """
    canary = "zzqx7canary sleep causes everything"
    me = await person(client)
    await seed_series(session, me.id, _series(days=15, seed=5, end=END))
    await seed(session, me.id, at=END, valence=2, energy=2, note=canary)

    patterns = await client.get(PATTERNS, headers=me.headers, params={"tz": "UTC"})
    weekly = await client.get(WEEKLY, headers=me.headers, params={"tz": "UTC"})
    export = await client.get(EXPORT, headers=me.headers, params={"tz": "UTC"})
    assert patterns.status_code == weekly.status_code == export.status_code == 200

    # The statistics never see the note, so its words cannot appear in them.
    for response in (patterns, weekly):
        assert canary not in response.text
        assert "sleep causes" not in response.text

    # The export is the person's own data, and the note belongs in it...
    body = export.json()
    assert canary in body["entries_csv"]
    # ...while the two computed bundles beside it are still note-free.
    assert canary not in body["factor_associations_csv"]
    assert canary not in body["time_patterns_csv"]


async def test_another_accounts_check_ins_are_invisible(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    end = END
    mine = await person(client)
    theirs = await person(client)
    await seed_series(session, mine.id, _series(days=40, seed=21, end=end))

    body = (await client.get(PATTERNS, headers=theirs.headers, params={"tz": "UTC"})).json()
    assert body["rolling_7"]["points"] == []
    assert all(a["with_factor_count"] == 0 for a in body["factor_associations"])
    assert all(a["without_factor_count"] == 0 for a in body["factor_associations"])

    weekly = (await client.get(WEEKLY, headers=theirs.headers)).json()
    assert weekly["total_check_ins"] == 0


async def test_auth_and_consent_are_required(client: httpx.AsyncClient) -> None:
    anonymous = await client.get(PATTERNS)
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "token_missing"

    unconsented = await person(client, kinds=())
    refused = await client.get(WEEKLY, headers=unconsented.headers)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "consent_required"


async def test_an_unknown_timezone_is_a_422_not_a_silent_utc(
    client: httpx.AsyncClient,
) -> None:
    me = await person(client)
    response = await client.get(PATTERNS, headers=me.headers, params={"tz": "Mars/Olympus"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_timezone"


async def test_the_response_echoes_the_zone_it_counted_in(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """An omitted zone is UTC, and the response admits it rather than shrugging."""
    end = END
    me = await person(client)
    await seed_series(session, me.id, _series(days=10, seed=2, end=end))

    silent = (await client.get(PATTERNS, headers=me.headers)).json()
    assert silent["timezone"] == "UTC"
    assert silent["timezone_fallback_used"] is True

    explicit = (
        await client.get(PATTERNS, headers=me.headers, params={"tz": "Asia/Kolkata"})
    ).json()
    assert explicit["timezone"] == "Asia/Kolkata"
    assert explicit["timezone_fallback_used"] is False


# --------------------------------------------------------------------------- #
# The rolling windows and the time patterns                                     #
# --------------------------------------------------------------------------- #


async def test_the_rolling_windows_are_the_size_they_say(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The 7-day window clips at seven days; the 30-day at thirty."""
    end = END
    me = await person(client)
    await seed_series(session, me.id, _series(days=45, seed=13, end=end))

    body = (await client.get(PATTERNS, headers=me.headers, params={"tz": "UTC"})).json()

    assert body["rolling_7"]["window_days"] == 7
    assert len(body["rolling_7"]["points"]) == 7
    assert body["rolling_30"]["window_days"] == 30
    assert len(body["rolling_30"]["points"]) == 30

    # Days are ISO dates, in order, and each point carries its own count.
    days = [point["day"] for point in body["rolling_7"]["points"]]
    assert days == sorted(days)
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) for day in days)
    assert all(point["count"] >= 1 for point in body["rolling_7"]["points"])
    assert all(point["valence_avg"] is not None for point in body["rolling_7"]["points"])


async def test_time_patterns_use_the_callers_local_hours(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """An instant near midnight belongs to a different hour in a different zone.

    18:30Z on the 14th is 00:00 on the 15th in Kolkata — an hour-of-day test that
    would pass in UTC and fail here if the zone were ignored.
    """
    me = await person(client)
    for offset in range(6):
        await seed(
            session,
            me.id,
            at=datetime(2026, 3, 14, 18, 30, tzinfo=UTC) + timedelta(minutes=offset),
            valence=2,
            energy=2,
        )

    utc = (await client.get(PATTERNS, headers=me.headers, params={"tz": "UTC"})).json()
    kolkata = (await client.get(PATTERNS, headers=me.headers, params={"tz": "Asia/Kolkata"})).json()

    utc_18 = next(p for p in utc["hourly_patterns"] if p["bucket"] == "18")
    kolkata_00 = next(p for p in kolkata["hourly_patterns"] if p["bucket"] == "00")

    assert utc_18["count"] == 6
    assert kolkata_00["count"] == 6

    # And the day itself moved for the same reason.
    assert utc["weekday_patterns"][5]["bucket"] == "Sat"
    assert kolkata["weekday_patterns"][6]["bucket"] == "Sun"
    assert kolkata["weekday_patterns"][6]["count"] == 6


# --------------------------------------------------------------------------- #
# Export                                                                        #
# --------------------------------------------------------------------------- #


async def test_export_is_csv_and_carries_its_own_headings(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    end = END
    me = await person(client)
    await seed_series(session, me.id, _series(days=20, seed=17, sleep_effect=-1.0, end=end))

    body = (await client.get(EXPORT, headers=me.headers, params={"tz": "UTC"})).json()

    entries = body["entries_csv"].splitlines()
    assert entries[0] == "local_date,created_at_utc,valence,energy,emotions,factors,note"
    assert len(entries) == 21  # header + 20 rows

    associations = body["factor_associations_csv"].splitlines()
    assert associations[0].startswith("factor,with_factor_avg,without_factor_avg")
    assert len(associations) == 9  # header + the eight tag rows

    patterns = body["time_patterns_csv"].splitlines()
    assert patterns[0] == "type,bucket,valence_avg,energy_avg,count,note"
    assert len(patterns) == 32  # header + 24 hours + 7 weekdays


async def test_export_never_contains_a_note_body(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The CSV is the person's own data — and a note is not a statistic.

    The note column exists in the export (it is their data), but the patterns and
    associations it sits beside are computed without it. Asserted from the other
    side here: the note text appears in exactly the entries bundle.
    """
    canary = "note-that-must-not-leak-9f2a"
    me = await person(client)
    await seed(session, me.id, at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC), note=canary)

    body = (await client.get(EXPORT, headers=me.headers)).json()
    assert canary in body["entries_csv"]  # their own note, in their own export
    assert canary not in body["factor_associations_csv"]
    assert canary not in body["time_patterns_csv"]


# --------------------------------------------------------------------------- #
# The false-positive sweep on the pure functions (fast, in-suite)               #
# --------------------------------------------------------------------------- #


def test_noise_never_looks_like_a_pattern() -> None:
    """Twenty noise series through the statistics themselves, no HTTP.

    This is the in-suite half of the false-positive measurement; the report with
    the printed rate lives in ``scripts/verify_day16.py``.
    """
    from app.services.mood.insights import factor_association

    strong = 0
    compared = 0
    for seed in range(20):
        rows = _series(days=45, seed=5000 + seed, end=END)
        entries: list[tuple[datetime, int, int, list[str], dict[str, object]]] = [
            (instant, valence, energy, [], dict.fromkeys(factors))
            for instant, valence, energy, factors in rows
        ]
        for association in factor_association(entries, zone="UTC", rng=random.Random(seed)):
            if association.p_value is None or association.effect_size is None:
                continue
            compared += 1
            if association.p_value < 0.05 and abs(association.effect_size) > 0.8:
                strong += 1

    rate = strong / compared if compared else 0.0
    assert compared > 20, "the sweep did not compare enough factors to mean anything"
    assert rate <= 0.10, f"{strong}/{compared} noise comparisons looked strong ({rate:.1%})"
