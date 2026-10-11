"""Real API, real SQLite: daily-plan generation, idempotence, done/skip, safety.

The plan endpoints are exercised through the same guest flow the other API
tests use: register, consent, call. Signals that need history (a low-mood
week, an old check-in) are backdated through the repositories, exactly the
rows the endpoints would have written on those days.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import MoodRepository
from app.db.repos.users import UserRepository
from app.models.cbt import CBTRecord

ROOT = "/api/v1/plan"
SETTINGS = "/api/v1/settings"


async def guest(
    client: httpx.AsyncClient, *, consent: bool = True
) -> tuple[uuid.UUID, dict[str, str]]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    if consent:
        requirements = (await client.get("/api/v1/consent/requirements")).json()["documents"]
        grants = [
            {"kind": item["kind"], "version": item["version"], "granted": True}
            for item in requirements
            if item["kind"] in ("terms", "privacy")
        ]
        assert (
            await client.post("/api/v1/consent", headers=headers, json={"grants": grants})
        ).status_code == 201
    return uuid.UUID(body["user"]["id"]), headers


async def backdate_checkins(
    session: AsyncSession, user_id: uuid.UUID, entries: list[tuple[int, int, list[str]]]
) -> None:
    """Check-ins ``days_ago`` days in the past, anchored at local noon.

    Noon UTC keeps every row inside its intended UTC day whatever the wall
    clock reads when the test runs, and the plan endpoints below all ask for
    UTC, so local day and UTC day agree.
    """
    now = datetime.now(UTC)
    repository = MoodRepository(session)
    for days_ago, valence, emotions in sorted(entries, reverse=True):
        anchor = (now - timedelta(days=days_ago)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        if anchor > now:  # "today at noon" is still in the future before noon
            anchor = now - timedelta(minutes=2)
        await repository.record(
            user_id=user_id,
            valence=valence,
            energy=3,
            emotions=emotions,
            created_at=anchor,
        )
    await session.commit()


async def test_today_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{ROOT}/today")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_missing"


async def test_today_requires_consent(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client, consent=False)
    response = await client.get(f"{ROOT}/today", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "consent_required"


async def test_done_and_skip_require_consent_too(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client, consent=False)
    item_id = uuid.uuid4()
    assert (await client.post(f"{ROOT}/{item_id}/done", headers=headers)).status_code == 403
    assert (await client.post(f"{ROOT}/{item_id}/skip", headers=headers)).status_code == 403


async def test_new_user_gets_one_gentle_suggestion_with_a_why(
    client: httpx.AsyncClient,
) -> None:
    _, headers = await guest(client)
    response = await client.get(f"{ROOT}/today", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == len(body["items"]) == 1
    item = body["items"][0]
    assert item["kind"] == "check_in"
    assert item["status"] == "pending"
    assert item["title"].strip()
    assert item["why"].strip()
    assert body["support_only"] is False
    assert body["quiet_hours_active"] is False


async def test_today_is_idempotent_and_keeps_status(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    first = (await client.get(f"{ROOT}/today", headers=headers)).json()
    item_id = first["items"][0]["id"]

    done = await client.post(f"{ROOT}/{item_id}/done", headers=headers)
    assert done.status_code == 200
    assert done.json()["status"] == "done"

    second = (await client.get(f"{ROOT}/today", headers=headers)).json()
    assert second["count"] == 1
    assert second["items"][0]["id"] == item_id, "a second GET must not re-roll the plan"
    assert second["items"][0]["status"] == "done"


async def test_skip_needs_no_body_and_changing_mind_is_allowed(
    client: httpx.AsyncClient,
) -> None:
    _, headers = await guest(client)
    item_id = (await client.get(f"{ROOT}/today", headers=headers)).json()["items"][0]["id"]

    skipped = await client.post(f"{ROOT}/{item_id}/skip", headers=headers)
    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"

    redone = await client.post(f"{ROOT}/{item_id}/done", headers=headers)
    assert redone.status_code == 200
    assert redone.json()["status"] == "done"


async def test_low_mood_week_produces_a_small_plan_with_reasons(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user_id, headers = await guest(client)
    recent: list[tuple[int, int, list[str]]] = [(0, 1, []), (1, 1, []), (2, 2, [])]
    prior: list[tuple[int, int, list[str]]] = [(3, 4, []), (4, 4, []), (5, 4, []), (6, 4, [])]
    await backdate_checkins(session, user_id, recent + prior)

    body = (await client.get(f"{ROOT}/today", headers=headers)).json()
    assert body["count"] <= 3
    kinds = {item["kind"] for item in body["items"]}
    assert "exercise" in kinds
    for item in body["items"]:
        assert item["why"].strip()
        assert item["title"].strip()


async def test_anxiety_tags_steer_the_plan_to_a_grounding_or_breathing_exercise(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user_id, headers = await guest(client)
    await backdate_checkins(session, user_id, [(0, 4, []), (1, 3, ["anxiety"])])

    body = (await client.get(f"{ROOT}/today", headers=headers)).json()
    exercises = [item for item in body["items"] if item["kind"] == "exercise"]
    assert exercises, body
    # Which of the three is picked depends on the requester's time of day;
    # the rule table in the unit tests pins each branch. All are appropriate.
    assert exercises[0]["ref"] in {"grounding-54321", "box-breathing", "slow-exhale-478"}


async def test_preference_tags_filter_anxiety_exercises(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user_id, headers = await guest(client)
    await client.put(
        "/api/v1/settings", headers=headers, json={"preferred_exercise_tags": ["breathing"]}
    )
    await backdate_checkins(session, user_id, [(0, 4, []), (1, 3, ["anxiety"])])

    body = (await client.get(f"{ROOT}/today", headers=headers)).json()
    exercises = [item for item in body["items"] if item["kind"] == "exercise"]
    assert exercises, body
    assert exercises[0]["ref"] in {"box-breathing", "slow-exhale-478"}, exercises


async def test_item9_positive_shows_support_and_nothing_else(
    client: httpx.AsyncClient,
) -> None:
    _, headers = await guest(client)
    answers = {f"phq9_{index}": 0 for index in range(1, 10)}
    answers["phq9_9"] = 1
    submit = await client.post(
        "/api/v1/assessments/phq9/submit", headers=headers, json={"answers": answers}
    )
    assert submit.status_code == 201
    assert submit.json()["crisis"] is not None, "item 9 above zero must open the crisis flow"

    body = (await client.get(f"{ROOT}/today", headers=headers)).json()
    assert body["support_only"] is True
    assert body["count"] == 1
    assert body["items"][0]["kind"] == "support"
    assert body["items"][0]["ref"] is None
    assert body["items"][0]["why"].strip()


async def test_item9_zero_does_not_make_the_plan_support_only(
    client: httpx.AsyncClient,
) -> None:
    _, headers = await guest(client)
    answers = {f"phq9_{index}": 0 for index in range(1, 10)}
    submit = await client.post(
        "/api/v1/assessments/phq9/submit", headers=headers, json={"answers": answers}
    )
    assert submit.status_code == 201
    assert submit.json()["crisis"] is None

    body = (await client.get(f"{ROOT}/today", headers=headers)).json()
    assert body["support_only"] is False
    assert all(item["kind"] != "support" for item in body["items"])


async def test_quiet_hours_leave_the_plan_empty(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    settings = await client.put(
        SETTINGS,
        headers=headers,
        json={
            "preferred_exercise_tags": [],
            "quiet_hours_enabled": True,
            "quiet_start": "00:00",
            "quiet_end": "23:59",
        },
    )
    assert settings.status_code == 200

    body = (await client.get(f"{ROOT}/today", headers=headers)).json()
    assert body["quiet_hours_active"] is True
    assert body["count"] == 0
    assert body["items"] == []


async def test_timezone_controls_the_local_day_and_invalid_zones_422(
    client: httpx.AsyncClient,
) -> None:
    _, headers = await guest(client)
    kolkata = await client.get(f"{ROOT}/today?tz=Asia/Kolkata", headers=headers)
    assert kolkata.status_code == 200
    assert kolkata.json()["timezone"] == "Asia/Kolkata"
    assert kolkata.json()["timezone_default_used"] is False

    bad = await client.get(f"{ROOT}/today?tz=Mars/Olympus", headers=headers)
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_timezone"


async def test_other_peoples_items_are_not_found(client: httpx.AsyncClient) -> None:
    _, headers_a = await guest(client)
    _, headers_b = await guest(client)
    item_id = (await client.get(f"{ROOT}/today", headers=headers_a)).json()["items"][0]["id"]

    for action in ("done", "skip"):
        response = await client.post(f"{ROOT}/{item_id}/{action}", headers=headers_b)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "plan_item_not_found"


async def test_unknown_item_is_not_found(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    response = await client.post(f"{ROOT}/{uuid.uuid4()}/done", headers=headers)
    assert response.status_code == 404


async def test_generated_plan_respects_the_three_item_cap(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Every signal at once: drop, anxiety, an old check-in, an open record."""
    user_id, headers = await guest(client)
    signals: list[tuple[int, int, list[str]]] = [
        (0, 1, ["anxiety"]),
        (1, 1, []),
        (2, 2, []),
    ] + [(day, 4, []) for day in (3, 4, 5, 6)]
    await backdate_checkins(session, user_id, signals)

    _, key_id = await UserRepository(session).ensure_data_key(user_id)
    session.add(
        CBTRecord(
            user_id=user_id,
            situation_encrypted=b"x",
            automatic_thought_encrypted=b"x",
            evidence_for_encrypted=b"x",
            evidence_against_encrypted=b"x",
            balanced_thought_encrypted=b"x",
            emotion="anxiety",
            intensity_before=60,
            intensity_after=40,
            what_helped=[],
            key_id=key_id,
        )
    )
    await session.commit()

    body = (await client.get(f"{ROOT}/today", headers=headers)).json()
    assert body["count"] <= 3
    assert len({item["kind"] for item in body["items"]}) == body["count"]
    assert all(item["why"].strip() for item in body["items"])


@pytest.mark.parametrize("action", ["done", "skip"])
async def test_action_on_non_uuid_path_is_a_validation_error(
    client: httpx.AsyncClient, action: str
) -> None:
    _, headers = await guest(client)
    response = await client.post(f"{ROOT}/not-a-uuid/{action}", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
