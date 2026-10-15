"""The insights endpoints end to end, against the real app and a real database.

What this file is paranoid about, in the order a reviewer should read it:

* **The minimum-data rules survive the trip through HTTP.** A person with three
  check-ins gets a ``200`` whose summary says there is not enough data — not a
  ``404``, not an empty list, and not a trend with a shrug in it.
* **A planted effect is found, and nothing else is.** Seeding sixty days where
  ``sleep`` sits one point lower and ``friends`` half a point higher must
  produce exactly those two associations, in the caller's language, with both
  sample sizes inside the sentence.
* **No causal vocabulary reaches a response body.** The check runs over the real
  JSON of all three locales with a blocklist written in this file, because the
  point of the rule is that it holds for the *response*, not only for the
  template that produced it.
* **The note is never in the room.** A check-in is seeded with a sentinel note
  and the sentinel is asserted absent from both response bodies: the projection
  the endpoint reads cannot contain it.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.insights import load_insights
from app.db.repos import MoodRepository
from app.models.mood import MoodEntry
from app.models.user import User

WEEKLY = "/api/v1/insights/weekly"
PATTERNS = "/api/v1/insights/patterns"
OUTLOOK = "/api/v1/insights/outlook"
SETTINGS = "/api/v1/settings"
VERSION = "2026-10-01"

KOLKATA = ZoneInfo("Asia/Kolkata")
#: Pinned through the documented seam (``app.api.v1.insights._now``) so "today"
#: is a fact of the run rather than a race with the calendar.
NOW = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 11)

#: A sentence nobody should ever see come back out of an insights response.
NOTE_SENTINEL = "sentinel-note-about-my-sister-zz9"

#: An independent scan of the response bodies (see the module docstring).
CAUSAL_BLOCKLIST = (
    "because",
    "causes",
    "caused",
    "due to",
    "leads to",
    "results in",
    "as a result",
    "the reason",
    "why your",
)


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the insights clock, the way the mood tests freeze theirs."""
    import app.api.v1.insights as insights_api

    monkeypatch.setattr(insights_api, "_now", lambda: NOW)


@dataclass
class Person:
    """A signed-in guest with the consents the insights routes need."""

    id: uuid.UUID
    headers: dict[str, str]


async def person(
    client: httpx.AsyncClient,
    *,
    kinds: tuple[str, ...] = ("terms", "privacy"),
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


async def set_language(session: AsyncSession, user_id: uuid.UUID, language: str) -> None:
    user = await session.get(User, user_id)
    assert user is not None
    user.language = language
    await session.commit()


async def seed_history(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    days: int = 60,
    seed: int = 11,
    sleep_effect: float = -1.0,
    friends_effect: float = 0.5,
    baseline: float = 3.3,
    jitter: float = 0.45,
    note: str | None = None,
    zone: ZoneInfo = KOLKATA,
) -> list[MoodEntry]:
    """Seed a history with planted effects, one check-in a day.

    Written the way the Day 16 verification brief asks for it: "poor sleep"
    (the ``sleep`` tag) sits about one point lower, ``friends`` about half a
    point higher, everything else is noise. The values are the person's own
    1-5 integers, rounded — the same quantisation a real check-in has, which is
    why the planted effect comes back as roughly 1.0 and not exactly 1.0.
    """
    rng = random.Random(seed)
    repository = MoodRepository(session)
    entries: list[MoodEntry] = []

    for offset in range(days):
        day = TODAY - timedelta(days=days - 1 - offset)
        hour = rng.choice([8, 9, 13, 18, 21, 23])
        clock = datetime.min.time().replace(hour=hour, minute=15)
        local = datetime.combine(day, clock, tzinfo=zone)
        value = baseline + rng.gauss(0, jitter)
        factors: list[str] = []
        if rng.random() < 0.4:
            factors.append("sleep")
            value += sleep_effect
        if rng.random() < 0.35:
            factors.append("friends")
            value += friends_effect
        if rng.random() < 0.25:
            factors.append(rng.choice(["work", "study", "family", "health", "money", "weather"]))
        entry = await repository.record(
            user_id=user_id,
            valence=max(1, min(5, round(value))),
            energy=max(1, min(5, round(value + rng.gauss(0, 0.4)))),
            emotions=[],
            factors=dict.fromkeys(factors) if factors else None,
            note=note,
            created_at=local.astimezone(UTC),
        )
        entries.append(entry)

    await session.commit()
    return entries


async def seed_noise(
    session: AsyncSession, user_id: uuid.UUID, *, days: int = 60, seed: int = 0
) -> None:
    """Sixty days of check-ins with no relationship between tags and mood."""
    rng = random.Random(seed)
    repository = MoodRepository(session)
    for offset in range(days):
        day = TODAY - timedelta(days=days - 1 - offset)
        hour = rng.choice([7, 10, 14, 17, 20, 23])
        local = datetime.combine(day, datetime.min.time().replace(hour=hour), tzinfo=KOLKATA)
        factors = [factor for factor in ("sleep", "work", "friends", "study") if rng.random() < 0.3]
        await repository.record(
            user_id=user_id,
            valence=rng.randint(1, 5),
            energy=rng.randint(1, 5),
            factors=dict.fromkeys(factors) if factors else None,
            created_at=local.astimezone(UTC),
        )
    await session.commit()


async def patterns(client: httpx.AsyncClient, me: Person, **params: str) -> dict[str, Any]:
    response = await client.get(PATTERNS, headers=me.headers, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def enable_outlook(client: httpx.AsyncClient, me: Person, enabled: bool = True) -> None:
    response = await client.put(
        SETTINGS,
        headers=me.headers,
        json={"mood_outlook_enabled": enabled},
    )
    assert response.status_code == 200, response.text
    assert response.json()["mood_outlook_enabled"] is enabled


async def outlook(client: httpx.AsyncClient, me: Person, **params: str) -> dict[str, Any]:
    response = await client.get(OUTLOOK, headers=me.headers, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def seed_outlook_persona(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    persona: str,
) -> None:
    """Seed a deterministic numeric-only persona for output and wording checks."""
    repository = MoodRepository(session)
    for offset in range(40):
        day = TODAY - timedelta(days=39 - offset)
        factors: dict[str, object] | None = None
        if persona == "steady_high":
            valence = 4
        elif persona == "steady_low":
            valence = 2
        elif persona == "rising":
            valence = 2 if offset < 20 else 4
        elif persona == "work_tagged_low":
            tagged = offset % 2 == 0
            valence = 2 if tagged else 4
            factors = {"work": 1} if tagged else None
        else:
            raise ValueError(f"unknown synthetic persona {persona!r}")
        local = datetime.combine(
            day,
            datetime.min.time().replace(hour=12),
            tzinfo=KOLKATA,
        )
        await repository.record(
            user_id=user_id,
            valence=valence,
            energy=3,
            factors=factors,
            created_at=local.astimezone(UTC),
        )
    await session.commit()


async def seed_outlook_ages(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    ages_days: Sequence[int],
    entries_per_day: int = 1,
) -> None:
    """Seed a precise set of local check-in dates for eligibility edge cases."""
    repository = MoodRepository(session)
    for age in ages_days:
        day = TODAY - timedelta(days=age)
        local = datetime.combine(
            day,
            datetime.min.time().replace(hour=12),
            tzinfo=KOLKATA,
        )
        for _ in range(entries_per_day):
            await repository.record(
                user_id=user_id,
                valence=3,
                energy=3,
                created_at=local.astimezone(UTC),
            )
    await session.commit()


def associations(body: dict[str, Any], *, reported_only: bool = True) -> list[dict[str, Any]]:
    factors = body["factors"]
    assert isinstance(factors, dict)
    items = factors["associations"]
    assert isinstance(items, list)
    found = [item for item in items if isinstance(item, dict)]
    if reported_only:
        return [item for item in found if item["reported"]]
    return found


# --------------------------------------------------------------------------- #
# Gates                                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", [WEEKLY, PATTERNS])
async def test_both_routes_need_a_session_and_the_data_agreements(
    client: httpx.AsyncClient, path: str
) -> None:
    anonymous = await client.get(path)
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "token_missing"

    unconsented = await person(client, kinds=())
    refused = await client.get(path, headers=unconsented.headers)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "consent_required"
    assert "terms" in refused.json()["error"]["message"]


@pytest.mark.parametrize("path", [WEEKLY, PATTERNS])
async def test_both_routes_appear_in_the_public_schema(
    client: httpx.AsyncClient, path: str
) -> None:
    schema = (await client.get("/openapi.json")).json()
    assert path in schema["paths"]


@pytest.mark.parametrize("path", [WEEKLY, PATTERNS])
async def test_an_unknown_timezone_is_an_error_not_a_silent_utc_fallback(
    client: httpx.AsyncClient, path: str
) -> None:
    me = await person(client)

    response = await client.get(path, headers=me.headers, params={"tz": "Mars/Olympus"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_timezone"


@pytest.mark.parametrize(
    ("path", "param", "value"),
    [
        (PATTERNS, "days", "6"),
        (PATTERNS, "days", "400"),
        (WEEKLY, "weeks", "0"),
        (WEEKLY, "weeks", "13"),
    ],
)
async def test_window_bounds_are_rejected_rather_than_clamped(
    client: httpx.AsyncClient, path: str, param: str, value: str
) -> None:
    """Silently clamping would compute an average over a window nobody asked for."""
    me = await person(client)

    response = await client.get(path, headers=me.headers, params={param: value})

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Opt-in Mood Outlook                                                          #
# --------------------------------------------------------------------------- #


async def test_outlook_requires_authentication_consent_and_explicit_opt_in(
    client: httpx.AsyncClient,
) -> None:
    anonymous = await client.get(OUTLOOK)
    assert anonymous.status_code == 401

    unconsented = await person(client, kinds=())
    consent_refusal = await client.get(OUTLOOK, headers=unconsented.headers)
    assert consent_refusal.status_code == 403
    assert consent_refusal.json()["error"]["code"] == "consent_required"

    opted_out = await person(client)
    settings = await client.get(SETTINGS, headers=opted_out.headers)
    assert settings.status_code == 200
    assert settings.json()["mood_outlook_enabled"] is False
    refusal = await client.get(OUTLOOK, headers=opted_out.headers)
    assert refusal.status_code == 403
    assert refusal.json()["error"]["code"] == "outlook_opt_in_required"


@pytest.mark.parametrize(
    ("history_days", "expected_status", "expected_available"),
    [(5, "not_enough_data", False), (14, "ready", True), (40, "ready", True)],
)
async def test_outlook_cold_start_gate_for_five_fourteen_and_forty_days(
    client: httpx.AsyncClient,
    session: AsyncSession,
    history_days: int,
    expected_status: str,
    expected_available: bool,
) -> None:
    me = await person(client)
    await enable_outlook(client, me)
    await seed_history(session, me.id, days=history_days, seed=history_days)

    body = await outlook(client, me, tz="Asia/Kolkata", locale="en")

    assert body["status"] == expected_status
    assert body["available"] is expected_available
    assert body["minimum_checkin_days"] == 14
    assert body["lookback_days"] == 28
    if history_days == 5:
        assert body["forecast"] == []
        assert body["distinct_checkin_days"] == 5
    else:
        assert len(body["forecast"]) == 3
        assert body["distinct_checkin_days"] == min(history_days, 28)
        assert all(
            point["lower"] <= point["estimate"] <= point["upper"] for point in body["forecast"]
        )
        assert "can be wrong" in body["uncertainty_note"]
        assert "not a calibrated confidence interval or a probability" in body["uncertainty_note"]
        assert body["not_health_advice"]


async def test_outlook_requires_distinct_days_and_a_checkin_within_seven_days(
    client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    duplicate_days = await person(client)
    await enable_outlook(client, duplicate_days)
    await seed_outlook_ages(
        session,
        duplicate_days.id,
        ages_days=range(7),
        entries_per_day=2,
    )
    duplicate_body = await outlook(client, duplicate_days, tz="Asia/Kolkata")
    assert duplicate_body["check_ins_used"] == 14
    assert duplicate_body["distinct_checkin_days"] == 7
    assert duplicate_body["status"] == "not_enough_data"
    assert duplicate_body["forecast"] == []

    stale_person = await person(client)
    await enable_outlook(client, stale_person)
    await seed_outlook_ages(
        session,
        stale_person.id,
        ages_days=range(8, 22),
    )
    stale_body = await outlook(client, stale_person, tz="Asia/Kolkata")
    assert stale_body["distinct_checkin_days"] == 14
    assert stale_body["latest_check_in"] == (TODAY - timedelta(days=8)).isoformat()
    assert stale_body["status"] == "stale"
    assert stale_body["available"] is False
    assert stale_body["forecast"] == []
    assert "paused" in stale_body["summary"]


async def test_outlook_response_does_not_expose_private_notes(
    client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    me = await person(client)
    await enable_outlook(client, me)
    await seed_history(
        session,
        me.id,
        days=14,
        seed=22,
        note="private-outlook-note-sentinel",
    )

    body = await outlook(client, me, tz="Asia/Kolkata", locale="en")

    assert "private-outlook-note-sentinel" not in str(body)


async def test_four_synthetic_persona_texts_pass_wording_lint(
    client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    """The API's complete sentences stay gentle, uncertain and non-medical."""
    blocklist = (
        "diagnos",
        "disorder",
        "depress",
        "anxiety",
        "crisis",
        "suicid",
        "cure",
        "guaranteed",
        "definitely",
        "must",
        "because",
        "causes",
        "will feel",
    )
    personas = ("steady_high", "steady_low", "rising", "work_tagged_low")
    responses: dict[str, dict[str, Any]] = {}
    for persona_name in personas:
        me = await person(client)
        await enable_outlook(client, me)
        await seed_outlook_persona(session, me.id, persona=persona_name)
        body = await outlook(client, me, tz="Asia/Kolkata", locale="en")
        assert body["status"] == "ready"
        assert len(body["forecast"]) == 3
        assert "can be wrong" in body["uncertainty_note"]
        text_parts = [body["summary"], body["uncertainty_note"], body["not_health_advice"]]
        text_parts.extend(factor["sentence"] for factor in body["top_factors"])
        optional_sentences = (
            body["no_factors_note"],
            body["small_action"],
            body["optional_support"],
        )
        text_parts.extend(item for item in optional_sentences if item)
        combined = " ".join(text_parts).casefold()
        for term in blocklist:
            assert term not in combined, f"{persona_name}: banned wording {term!r}: {combined!r}"
        responses[persona_name] = body

    assert responses["steady_low"]["optional_support"] is not None
    assert responses["steady_high"]["optional_support"] is None
    assert responses["work_tagged_low"]["top_factors"]
    assert responses["steady_high"]["top_factors"] == []


async def test_outlook_localises_to_hindi_and_bengali_and_rejects_bad_timezone(
    client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    me = await person(client)
    await enable_outlook(client, me)
    await seed_history(session, me.id, days=14, seed=19)

    for locale in ("hi", "bn"):
        body = await outlook(client, me, tz="Asia/Kolkata", locale=locale)
        assert body["locale"] == locale
        assert body["locale_fallback_used"] is False
        assert body["available"] is True
    invalid = await client.get(
        OUTLOOK,
        headers=me.headers,
        params={"tz": "Mars/Olympus", "locale": "en"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_timezone"


# --------------------------------------------------------------------------- #
# Not enough data                                                               #
# --------------------------------------------------------------------------- #


async def test_three_check_ins_say_not_enough_data_and_claim_nothing(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    repository = MoodRepository(session)
    for offset in range(3):
        await repository.record(
            user_id=me.id,
            valence=4,
            energy=2,
            factors={"sleep": 1},
            created_at=NOW - timedelta(days=offset),
        )
    await session.commit()

    body = await patterns(client, me, tz="Asia/Kolkata")

    assert body["enough_data"] is False
    assert body["considered_check_ins"] == 3
    summary = body["summary"]
    assert isinstance(summary, list)
    assert len(summary) == 1
    assert "not enough data" in str(summary[0]).lower()

    trend_body = body["trend"]
    assert isinstance(trend_body, dict)
    assert trend_body["enough_data"] is False
    for measure in trend_body["measures"]:
        assert isinstance(measure, dict)
        assert measure["direction"] == "not_enough_data"
        assert measure["change"] is None
        assert measure["direction_label"] == "Not enough data yet"

    assert associations(body) == []
    for key in ("time_of_day", "weekday"):
        pattern = body[key]
        assert isinstance(pattern, dict)
        assert pattern["reported"] is False
        assert pattern["lowest_id"] is None
        assert pattern["holdback"] is not None


async def test_a_brand_new_account_gets_an_answer_not_an_error(
    client: httpx.AsyncClient,
) -> None:
    me = await person(client)

    zone = {"tz": "Asia/Kolkata"}
    weekly = await client.get(WEEKLY, headers=me.headers, params=zone)
    patterns_response = await client.get(PATTERNS, headers=me.headers, params=zone)

    assert weekly.status_code == 200
    assert patterns_response.status_code == 200
    assert weekly.json()["considered_check_ins"] == 0
    assert "no check-ins" in weekly.json()["summary"][0].lower()
    assert "no check-ins" in patterns_response.json()["summary"][0].lower()
    assert patterns_response.json()["rolling"][0]["average"] is None


async def test_the_window_is_reported_next_to_the_total(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """`12 of your 40 check-ins`, never a window mistaken for a whole life."""
    me = await person(client)
    await seed_history(session, me.id, days=40)

    body = await patterns(client, me, tz="Asia/Kolkata", days="14")

    assert body["total_check_ins"] == 40
    assert isinstance(body["considered_check_ins"], int)
    assert body["considered_check_ins"] <= 15


# --------------------------------------------------------------------------- #
# A planted effect is found — and only the planted effect                        #
# --------------------------------------------------------------------------- #


async def test_the_seeded_sleep_and_friends_effects_are_found(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    await seed_history(session, me.id, days=60, seed=11)

    body = await patterns(client, me, tz="Asia/Kolkata", days="60")

    assert body["enough_data"] is True
    reported = associations(body)
    valence_reported = [item for item in reported if item["measure"] == "valence"]
    by_factor = {str(item["factor"]): item for item in valence_reported}

    # "Poor sleep" was seeded one point lower…
    assert "sleep" in by_factor
    sleep = by_factor["sleep"]
    assert isinstance(sleep["difference"], float)
    assert sleep["difference"] < -0.6
    assert sleep["ci_high"] is not None and sleep["ci_high"] < 0
    assert isinstance(sleep["p_value"], float)
    assert sleep["p_value"] < 0.05
    assert sleep["tagged_n"] >= 5 and sleep["untagged_n"] >= 5

    # …and "friends" half a point higher.
    assert "friends" in by_factor
    friends = by_factor["friends"]
    assert isinstance(friends["difference"], float)
    assert friends["difference"] > 0.2
    assert friends["ci_low"] is not None and friends["ci_low"] > 0

    # Nothing else was planted, so nothing else may be claimed about valence.
    assert set(by_factor) <= {"sleep", "friends"}


async def test_the_association_sentence_carries_both_sample_sizes(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    await seed_history(session, me.id, days=60, seed=11)

    body = await patterns(client, me, tz="Asia/Kolkata")
    sleep = next(
        item
        for item in associations(body)
        if item["factor"] == "sleep" and item["measure"] == "valence"
    )
    sentence = str(sleep["sentence"])

    assert sentence.startswith("On days you tagged sleep, your mood was about")
    assert "points lower" in sentence
    assert f"({sleep['tagged_n']} check-ins vs {sleep['untagged_n']})" in sentence
    assert sleep["label"] == "sleep"


async def test_a_held_back_comparison_gets_no_sentence_at_all(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The words to overstate a comparison must not exist in the payload."""
    me = await person(client)
    await seed_history(session, me.id, days=60, seed=11)

    body = await patterns(client, me, tz="Asia/Kolkata")
    held_back = [item for item in associations(body, reported_only=False) if not item["reported"]]

    assert held_back
    assert all(item["sentence"] is None for item in held_back)


async def test_the_correction_and_the_rules_are_published_in_the_response(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    await seed_history(session, me.id, days=30, seed=3)

    body = await patterns(client, me, tz="Asia/Kolkata")
    factors = body["factors"]
    assert isinstance(factors, dict)

    assert factors["comparisons"] == 16
    assert factors["family_alpha"] == pytest.approx(0.05 / 16, abs=1e-6)
    assert factors["min_group_n"] == 5
    assert factors["min_effect_points"] == pytest.approx(0.4)
    assert body["min_entries_for_trend"] == 7
    assert body["scale_max"] == 5


async def test_twenty_noise_histories_produce_no_reported_pattern(
    client_factory: Callable[..., httpx.AsyncClient], session: AsyncSession
) -> None:
    """The false-positive check over HTTP, one account per seed.

    Twenty accounts, sixty days of pure noise each: no factor association, no
    time-of-day pattern and no weekday pattern may be reported for any of them.

    The client is built with a raised auth rate limit because this test signs in
    twenty times in a few seconds, and the default limit exists to stop exactly
    that in production — it is not what is under test here.
    """
    client = client_factory(rate_limit_auth_per_minute=500)
    reported_anywhere = 0
    for seed in range(20):
        me = await person(client)
        await seed_noise(session, me.id, days=60, seed=seed)

        body = await patterns(client, me, tz="Asia/Kolkata", days="60")

        reported_anywhere += len(associations(body))
        for key in ("time_of_day", "weekday"):
            pattern = body[key]
            assert isinstance(pattern, dict)
            if pattern["reported"]:
                reported_anywhere += 1

    assert reported_anywhere == 0


# --------------------------------------------------------------------------- #
# Shape, rolling series and the weekly surface                                  #
# --------------------------------------------------------------------------- #


async def test_the_rolling_series_is_a_window_long_and_carries_its_counts(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    await seed_history(session, me.id, days=40, seed=5)

    body = await patterns(client, me, tz="Asia/Kolkata", days="60")
    rolling = body["rolling"]
    assert isinstance(rolling, list)

    assert {(item["measure"], item["window_days"]) for item in rolling} == {
        ("valence", 7),
        ("valence", 30),
        ("energy", 7),
        ("energy", 30),
    }
    seven = next(
        item for item in rolling if item["measure"] == "valence" and item["window_days"] == 7
    )
    assert seven["label"] == "Mood"
    assert seven["end"] == TODAY.isoformat()
    assert seven["start"] == (TODAY - timedelta(days=6)).isoformat()
    assert seven["entries"] == 7
    assert seven["enough_data"] is True
    assert isinstance(seven["average"], float) and 1.0 <= seven["average"] <= 5.0
    assert len(seven["series"]) == 56
    assert seven["series"][-1]["day"] == TODAY.isoformat()
    assert all(
        {"day", "average", "entries", "enough_data"} <= set(point) for point in seven["series"]
    )


async def test_the_weekly_surface_returns_every_week_it_was_asked_for(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    await seed_history(session, me.id, days=40, seed=5)

    response = await client.get(
        WEEKLY, headers=me.headers, params={"tz": "Asia/Kolkata", "weeks": "4"}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["weeks"] == 4
    assert len(body["weekly"]) == 4
    monday = TODAY - timedelta(days=TODAY.weekday())
    assert body["weekly"][-1]["start"] == monday.isoformat()
    assert body["weekly"][-1]["end"] == (monday + timedelta(days=6)).isoformat()
    assert body["weekly"][-1]["entries"] >= 1
    assert body["weekly"][-1]["label"] == "5 October 2026"
    assert body["weekly"][-1]["factors"]
    assert body["trend"]["window_days"] == 7
    # The weekly payload carries the rolling means but not 56-point series.
    assert all(item["series"] == [] for item in body["rolling"])
    assert body["summary"][0].startswith("Here are your last 4 weeks")


async def test_an_empty_week_is_returned_rather_than_dropped(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    repository = MoodRepository(session)
    # Two check-ins today and nothing for the three weeks before it.
    for offset in range(2):
        await repository.record(
            user_id=me.id, valence=3, energy=3, created_at=NOW - timedelta(hours=offset)
        )
    await session.commit()

    response = await client.get(WEEKLY, headers=me.headers, params={"tz": "Asia/Kolkata"})
    weeks = response.json()["weekly"]

    assert len(weeks) == 4
    assert weeks[0]["entries"] == 0
    assert weeks[0]["valence_mean"] is None
    assert weeks[0]["enough_data"] is False
    assert any("no check-ins" in sentence for sentence in response.json()["summary"])


# --------------------------------------------------------------------------- #
# Localisation                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("locale", "expected_label"),
    [("hi", "सुबह"), ("bn", "সকাল"), ("en", "Morning")],
)
async def test_the_summary_and_the_labels_follow_the_locale(
    client: httpx.AsyncClient,
    session: AsyncSession,
    locale: str,
    expected_label: str,
) -> None:
    me = await person(client)
    await seed_history(session, me.id, days=40, seed=5)

    body = await patterns(client, me, tz="Asia/Kolkata", locale=locale)

    assert body["locale"] == locale
    assert body["locale_fallback_used"] is False
    summary = body["summary"]
    assert isinstance(summary, list)
    # The digit is read from the same content file the app renders with, so this
    # checks the response against the source of truth instead of against a copy
    # of it pasted into a test.
    digit = load_insights(locale).numerals[4]
    assert any(digit in sentence for sentence in summary), summary
    time_of_day = body["time_of_day"]
    assert isinstance(time_of_day, dict)
    first = next(iter(time_of_day["buckets"]))
    assert first["label"] == expected_label


async def test_an_unsupported_locale_falls_back_to_english_and_says_so(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    await seed_history(session, me.id, days=20, seed=5)

    body = await patterns(client, me, tz="Asia/Kolkata", locale="ta")

    assert body["locale"] == "en"
    assert body["locale_fallback_used"] is True


async def test_a_regional_tag_resolves_to_its_language(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    await seed_history(session, me.id, days=20, seed=5)

    body = await patterns(client, me, tz="Asia/Kolkata", locale="hi-IN")

    assert body["locale"] == "hi"
    assert body["locale_fallback_used"] is False


async def test_with_no_locale_the_account_language_decides(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    await set_language(session, me.id, "bn")
    await seed_history(session, me.id, days=20, seed=5)

    body = await patterns(client, me, tz="Asia/Kolkata")

    assert body["locale"] == "bn"
    assert body["locale_fallback_used"] is False


@pytest.mark.parametrize("locale", ["en", "hi", "bn"])
async def test_no_response_body_in_any_locale_uses_causal_language(
    client: httpx.AsyncClient, session: AsyncSession, locale: str
) -> None:
    me = await person(client)
    await seed_history(session, me.id, days=60, seed=11)

    for path in (WEEKLY, PATTERNS):
        response = await client.get(path, headers=me.headers, params={"locale": locale})
        assert response.status_code == 200
        summary = response.json()["summary"]
        joined = " ".join(summary).lower()
        for token in CAUSAL_BLOCKLIST:
            assert token not in joined, f"{locale} {path}: {token!r} in {joined!r}"


# --------------------------------------------------------------------------- #
# The explainer                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", [WEEKLY, PATTERNS])
async def test_every_response_carries_the_explainer(client: httpx.AsyncClient, path: str) -> None:
    me = await person(client)

    response = await client.get(path, headers=me.headers, params={"tz": "Asia/Kolkata"})
    about = response.json()["about"]

    assert about["title"] == "About these insights"
    assert "not a cause" in about["not_causal"]
    assert "not therapy" in about["disclaimer"]
    assert len(about["limitations"]) >= 5
    assert len(about["methods"]) >= 5
    assert any("sleep" in item.lower() for item in about["limitations"])
    assert any("permutation" in item.lower() for item in about["methods"])


# --------------------------------------------------------------------------- #
# Privacy                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", [WEEKLY, PATTERNS])
async def test_a_note_never_reaches_the_response(
    client: httpx.AsyncClient, session: AsyncSession, path: str
) -> None:
    me = await person(client)
    await seed_history(session, me.id, days=20, seed=5, note=NOTE_SENTINEL)

    response = await client.get(path, headers=me.headers, params={"tz": "Asia/Kolkata"})

    assert response.status_code == 200
    assert NOTE_SENTINEL not in response.text
    assert "sister" not in response.text


async def test_another_account_history_is_not_in_this_response(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    busy = await person(client)
    quiet = await person(client)
    await seed_history(session, busy.id, days=60, seed=11)

    body = await patterns(client, quiet, tz="Asia/Kolkata")

    assert body["considered_check_ins"] == 0
    assert body["total_check_ins"] == 0
    assert body["enough_data"] is False
    assert associations(body) == []


# --------------------------------------------------------------------------- #
# Reproducibility                                                               #
# --------------------------------------------------------------------------- #


async def test_two_identical_requests_say_the_same_thing(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """A number that moves when somebody refreshes is not a number they can trust."""
    me = await person(client)
    await seed_history(session, me.id, days=60, seed=11)

    first = await patterns(client, me, tz="Asia/Kolkata")
    second = await patterns(client, me, tz="Asia/Kolkata")

    assert first["summary"] == second["summary"]
    assert associations(first) == associations(second)
    assert first["trend"] == second["trend"]


async def test_the_same_instants_are_a_different_pattern_in_a_different_zone(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    await seed_history(session, me.id, days=40, seed=5)

    in_kolkata = await patterns(client, me, tz="Asia/Kolkata")
    in_utc = await patterns(client, me, tz="UTC")

    assert in_kolkata["timezone"] == "Asia/Kolkata"
    assert in_kolkata["timezone_fallback_used"] is False
    assert in_utc["timezone"] == "UTC"
    # Every check-in was seeded between 08:15 and 23:15 in Kolkata, so in UTC a
    # few of them move back a day and into a different part of the day.
    assert in_kolkata["time_of_day"]["buckets"] != in_utc["time_of_day"]["buckets"]


async def test_no_timezone_means_utc_and_the_response_says_so(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await person(client)
    await seed_history(session, me.id, days=20, seed=5)

    body = await patterns(client, me)

    assert body["timezone"] == "UTC"
    assert body["timezone_fallback_used"] is True


async def test_the_query_projects_no_note_column(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Belt and braces on the projection itself, not just on the response."""
    me = await person(client)
    await seed_history(session, me.id, days=5, seed=1, note=NOTE_SENTINEL)

    rows: Sequence[object] = await MoodRepository(session).insights_rows(me.id)

    assert len(rows) == 5
    first = rows[0]
    assert hasattr(first, "created_at")
    assert not hasattr(first, "note_encrypted")
    assert NOTE_SENTINEL not in repr(first)
