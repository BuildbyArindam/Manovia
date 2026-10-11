"""Real API, real SQLite: content, strict ratings, ownership, retry, and erasure."""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos.users import UserRepository
from app.models.exercise_completion import ExerciseCompletion

ROOT = "/api/v1/exercises"


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


async def count(session: AsyncSession) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(ExerciseCompletion))).scalar_one()
    )


async def test_content_and_why_are_public_and_static_routes_do_not_shadow_ids(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(ROOT)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == len(body["exercises"]) == 8
    for exercise in body["exercises"]:
        detail = await client.get(f"{ROOT}/{exercise['id']}")
        assert detail.status_code == 200
        assert detail.json() == exercise
        assert exercise["title"]["en"]
        assert exercise["contraindications"] and exercise["evidence_note"]
        assert (
            sum(step["duration_seconds"] for step in exercise["steps"])
            == exercise["duration_seconds"]
        )
    suggestions = await client.get(f"{ROOT}/recommendations?emotion=anxiety&intensity=high")
    assert suggestions.status_code == 200
    assert [item["exercise_id"] for item in suggestions.json()["recommendations"]] == [
        "grounding-54321",
        "box-breathing",
    ]
    assert all(
        "You selected high anxiety" in item["why"] for item in suggestions.json()["recommendations"]
    )


async def test_completion_is_persisted_with_ratings_and_appears_in_history(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user_id, headers = await guest(client)
    completion_id = uuid.uuid4()
    response = await client.post(
        f"{ROOT}/box-breathing/complete",
        headers=headers,
        json={"completion_id": str(completion_id), "pre_rating": 2, "post_rating": 4},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(completion_id)
    assert body["change"] == 2
    assert "2 points higher" in body["summary"]
    assert "not proof" in body["summary"]
    assert body["duration_seconds"] == 120
    assert len(body["definition_sha256"]) == 64
    assert body["created_at"].endswith("Z")
    row = await session.get(ExerciseCompletion, completion_id)
    assert row is not None and row.user_id == user_id
    assert (row.pre_rating, row.post_rating) == (2, 4)
    assert row.exercise_id == "box-breathing"
    history = (await client.get(f"{ROOT}/history", headers=headers)).json()
    assert history["items"] == [body]
    assert history["count"] == history["total"] == 1
    assert not history["has_more"]
    assert "user_id" not in body


@pytest.mark.parametrize(
    "ratings",
    [{}, {"pre_rating": None, "post_rating": None}, {"pre_rating": 2}, {"post_rating": 5}],
)
async def test_both_ratings_are_optional_and_missing_values_have_no_delta(
    client: httpx.AsyncClient, ratings: dict[str, object]
) -> None:
    _, headers = await guest(client)
    response = await client.post(f"{ROOT}/body-scan/complete", headers=headers, json=ratings)
    assert response.status_code == 201
    assert response.json()["change"] is None
    assert "Both optional ratings" in response.json()["summary"]


@pytest.mark.parametrize(
    ("pre", "post", "change", "words"), [(5, 1, -4, "4 points lower"), (3, 3, 0, "same rating")]
)
async def test_lower_and_unchanged_ratings_are_reported_without_failure_wording(
    client: httpx.AsyncClient, pre: int, post: int, change: int, words: str
) -> None:
    _, headers = await guest(client)
    response = await client.post(
        f"{ROOT}/body-scan/complete", headers=headers, json={"pre_rating": pre, "post_rating": post}
    )
    assert response.status_code == 201
    assert response.json()["change"] == change
    assert words in response.json()["summary"]
    assert "not proof" in response.json()["summary"]


@pytest.mark.parametrize("field", ["pre_rating", "post_rating"])
@pytest.mark.parametrize("value", [0, 6, -1, 3.5, "3", True])
async def test_bad_ratings_are_rejected_without_a_row(
    client: httpx.AsyncClient, session: AsyncSession, field: str, value: object
) -> None:
    _, headers = await guest(client)
    response = await client.post(f"{ROOT}/body-scan/complete", headers=headers, json={field: value})
    assert response.status_code == 422
    assert await count(session) == 0


@pytest.mark.parametrize(
    "body",
    [{"note": "not accepted"}, {"planner_choice": "not accepted"}, {"completion_id": "bad-uuid"}],
)
async def test_no_free_text_or_bad_id_is_accepted(
    client: httpx.AsyncClient, session: AsyncSession, body: dict[str, str]
) -> None:
    _, headers = await guest(client)
    response = await client.post(f"{ROOT}/body-scan/complete", headers=headers, json=body)
    assert response.status_code == 422
    assert await count(session) == 0


async def test_saves_and_history_need_authentication_and_current_consent(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get(f"{ROOT}/history")).status_code == 401
    assert (await client.post(f"{ROOT}/body-scan/complete", json={})).status_code == 401
    _, headers = await guest(client, consent=False)
    assert (await client.get(f"{ROOT}/history", headers=headers)).status_code == 403
    assert (
        await client.post(f"{ROOT}/body-scan/complete", headers=headers, json={})
    ).status_code == 403


async def test_unknown_ids_and_bad_recommendation_parameters(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    assert (await client.get(f"{ROOT}/missing")).status_code == 404
    assert (
        await client.post(f"{ROOT}/missing/complete", headers=headers, json={})
    ).status_code == 404
    for query in ("emotion=diagnosis", "emotion=anxiety&intensity=urgent", "intensity=high"):
        assert (await client.get(f"{ROOT}/recommendations?{query}")).status_code == 422


async def test_replay_is_idempotent_but_changed_ratings_or_exercise_conflict(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, headers = await guest(client)
    body = {"completion_id": str(uuid.uuid4()), "pre_rating": 2, "post_rating": 3}
    first = await client.post(f"{ROOT}/box-breathing/complete", headers=headers, json=body)
    second = await client.post(f"{ROOT}/box-breathing/complete", headers=headers, json=body)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json() == second.json()
    changed = await client.post(
        f"{ROOT}/box-breathing/complete", headers=headers, json={**body, "post_rating": 5}
    )
    different = await client.post(f"{ROOT}/body-scan/complete", headers=headers, json=body)
    assert changed.status_code == different.status_code == 409
    assert await count(session) == 1


async def test_simultaneous_retries_record_exactly_one_completion(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, headers = await guest(client)
    body = {"completion_id": str(uuid.uuid4()), "pre_rating": 2, "post_rating": 3}
    responses = await asyncio.gather(
        *[client.post(f"{ROOT}/body-scan/complete", headers=headers, json=body) for _ in range(2)]
    )
    assert sorted(response.status_code for response in responses) == [200, 201]
    assert responses[0].json() == responses[1].json()
    assert await count(session) == 1


async def test_history_and_completion_id_replay_are_owner_scoped(client: httpx.AsyncClient) -> None:
    _, first = await guest(client)
    _, other = await guest(client)
    body = {"completion_id": str(uuid.uuid4()), "pre_rating": 2, "post_rating": 4}
    saved = await client.post(f"{ROOT}/body-scan/complete", headers=first, json=body)
    assert saved.status_code == 201
    history = await client.get(f"{ROOT}/history", headers=other)
    assert history.json()["items"] == [] and history.json()["total"] == 0
    replay = await client.post(f"{ROOT}/body-scan/complete", headers=other, json=body)
    assert replay.status_code == 409
    assert "summary" not in replay.json()
    assert saved.json()["created_at"] not in replay.text


async def test_paginated_history_and_bounds(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    for _ in range(3):
        assert (
            await client.post(f"{ROOT}/body-scan/complete", headers=headers, json={})
        ).status_code == 201
    first = (await client.get(f"{ROOT}/history?limit=2", headers=headers)).json()
    last = (await client.get(f"{ROOT}/history?limit=2&offset=2", headers=headers)).json()
    assert first["count"] == 2 and first["total"] == 3 and first["has_more"]
    assert last["count"] == 1 and last["total"] == 3 and not last["has_more"]
    assert set(item["id"] for item in first["items"]).isdisjoint(
        item["id"] for item in last["items"]
    )
    for query in ("limit=0", "limit=101", "offset=-1"):
        assert (await client.get(f"{ROOT}/history?{query}", headers=headers)).status_code == 422


async def test_hard_account_deletion_erases_exercise_history(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user_id, headers = await guest(client)
    assert (
        await client.post(f"{ROOT}/body-scan/complete", headers=headers, json={})
    ).status_code == 201
    assert await count(session) == 1
    assert await UserRepository(session).hard_delete(user_id)
    await session.commit()
    assert await count(session) == 0
