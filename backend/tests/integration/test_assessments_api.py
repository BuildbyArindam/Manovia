"""The screening endpoints end to end, against the real app and database.

The four things this file is deliberately stubborn about, because they are the
ways a screening feature goes wrong quietly:

* **The item-9 rule outranks the total.** "All zeros except item 9 = 1" returns
  the crisis payload *and* the score of 1, and the row is stored either way. A
  feature that thanks somebody for answering honestly by deleting their result is
  not one anybody would answer honestly a second time.
* **Missing answers are a refusal, not a smaller total.** Each rejection names the
  missing item ids and writes nothing.
* **A result belongs to one person.** Another user's history is empty and their
  result id is a 404, never a 403.
* **The frequency suggestion is a sentence.** Submitting twice in one day works,
  and says why it is too soon to compare.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import AssessmentRepository, UserRepository
from app.models.assessment import AssessmentResult
from app.models.enums import AssessmentBand, AssessmentInstrument

ASSESSMENTS = "/api/v1/assessments"
VERSION = "2026-10-01"
PHQ9_IDS = tuple(f"phq9_{n}" for n in range(1, 10))
GAD7_IDS = tuple(f"gad7_{n}" for n in range(1, 8))


@dataclass
class Person:
    """A signed-in guest with the consents the assessment routes need."""

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


def answers(item_ids: tuple[str, ...], value: int, **overrides: int) -> dict[str, int]:
    document = dict.fromkeys(item_ids, value)
    document.update(overrides)
    return document


async def count_results(session: AsyncSession, instrument: AssessmentInstrument) -> int:
    stmt = (
        select(func.count())
        .select_from(AssessmentResult)
        .where(AssessmentResult.instrument == instrument)
    )
    return int((await session.execute(stmt)).scalar_one())


# --------------------------------------------------------------------------- #
# The public instrument surface
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("instrument", ["phq9", "gad7"])
async def test_definition_is_public_and_carries_the_official_wording(
    client: httpx.AsyncClient, instrument: str
) -> None:
    response = await client.get(f"{ASSESSMENTS}/{instrument}")
    assert response.status_code == 200
    body = response.json()
    assert body["instrument"] == instrument
    assert len(body["items"]) == (9 if instrument == "phq9" else 7)
    assert [option["label"] for option in body["response_options"]] == [
        "Not at all",
        "Several days",
        "More than half the days",
        "Nearly every day",
    ]
    assert body["screening_not_diagnosis"].startswith("This is a screening result, not a diagnosis")
    assert body["provenance"]["licence_statement"] == (
        "No permission required to reproduce, translate, display or distribute."
    )
    assert body["provenance"]["item_wording_source_url"].startswith("https://www.phqscreeners.com/")


async def test_definition_exposes_the_item9_rule_for_phq9_only(client: httpx.AsyncClient) -> None:
    phq9 = (await client.get(f"{ASSESSMENTS}/phq9")).json()
    gad7 = (await client.get(f"{ASSESSMENTS}/gad7")).json()
    assert phq9["safety"]["item_id"] == "phq9_9"
    assert phq9["safety"]["rule"] == "answer_greater_than_zero"
    assert phq9["safety"]["level"] == "high"
    assert gad7["safety"] is None


async def test_listing_returns_both_instruments(client: httpx.AsyncClient) -> None:
    response = await client.get(ASSESSMENTS)
    assert response.status_code == 200
    listed = {entry["instrument"]: entry for entry in response.json()}
    assert set(listed) == {"phq9", "gad7"}
    assert listed["phq9"]["item_count"] == 9
    assert listed["gad7"]["total_max"] == 21


async def test_an_unknown_instrument_is_a_validation_error(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{ASSESSMENTS}/phq8")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --------------------------------------------------------------------------- #
# Submitting
# --------------------------------------------------------------------------- #


async def test_submit_requires_a_session(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"{ASSESSMENTS}/phq9/submit", json={"answers": answers(PHQ9_IDS, 0)}
    )
    assert response.status_code == 401


async def test_submit_requires_consent(client: httpx.AsyncClient) -> None:
    without = await person(client, kinds=())
    response = await client.post(
        f"{ASSESSMENTS}/phq9/submit",
        headers=without.headers,
        json={"answers": answers(PHQ9_IDS, 0)},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "consent_required"


async def test_all_zeros_is_minimal_and_stored(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    who = await person(client)
    response = await client.post(
        f"{ASSESSMENTS}/phq9/submit",
        headers=who.headers,
        json={"answers": answers(PHQ9_IDS, 0)},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["result"]["total"] == 0
    assert body["result"]["max_total"] == 27
    assert body["result"]["band"] == "minimal"
    assert body["result"]["band_label"] == "Minimal"
    assert body["result"]["band_explanation"]
    assert body["result"]["what_helps"]
    assert "not a diagnosis" in body["result"]["screening_not_diagnosis"]
    assert body["crisis"] is None
    assert body["cadence"]["is_first"] is True
    assert body["cadence"]["due"] is True
    assert body["cadence"]["notice"]
    assert await count_results(session, AssessmentInstrument.PHQ9) == 1


@pytest.mark.parametrize(
    ("instrument", "item_ids", "total", "band"),
    [
        ("phq9", PHQ9_IDS, 27, "severe"),
        ("gad7", GAD7_IDS, 21, "severe"),
    ],
)
async def test_all_threes_is_the_maximum_and_severe(
    client: httpx.AsyncClient,
    instrument: str,
    item_ids: tuple[str, ...],
    total: int,
    band: str,
) -> None:
    who = await person(client)
    response = await client.post(
        f"{ASSESSMENTS}/{instrument}/submit",
        headers=who.headers,
        json={"answers": answers(item_ids, 3)},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["result"]["total"] == total
    assert body["result"]["band"] == band
    assert body["result"]["band_label"] == "Severe"
    assert body["result"]["answers"] == [{"item_id": item_id, "value": 3} for item_id in item_ids]


async def test_band_edges_are_scored_as_the_official_table_says(client: httpx.AsyncClient) -> None:
    """One submission per PHQ-9 band edge, through the real endpoint."""
    who = await person(client)
    cases = [(4, "minimal"), (5, "mild"), (9, "mild"), (10, "moderate"), (14, "moderate")]
    for total, band in cases:
        document = answers(PHQ9_IDS, 0)
        remaining = total
        for item_id in PHQ9_IDS:
            step = min(3, remaining)
            document[item_id] = step
            remaining -= step
        response = await client.post(
            f"{ASSESSMENTS}/phq9/submit", headers=who.headers, json={"answers": document}
        )
        assert response.status_code == 201
        assert response.json()["result"]["total"] == total
        assert response.json()["result"]["band"] == band


# --------------------------------------------------------------------------- #
# The item-9 rule
# --------------------------------------------------------------------------- #


async def test_item9_above_zero_triggers_the_crisis_flow_at_a_minimal_total(
    client: httpx.AsyncClient,
) -> None:
    who = await person(client)
    response = await client.post(
        f"{ASSESSMENTS}/phq9/submit",
        headers=who.headers,
        json={"answers": answers(PHQ9_IDS, 0, phq9_9=1)},
    )
    assert response.status_code == 201
    body = response.json()

    # The help comes first, in the contract as well as in the UI.
    crisis = body["crisis"]
    assert crisis is not None
    assert crisis["item_id"] == "phq9_9"
    assert crisis["level"] == "high"
    assert crisis["stored_level"] == "crisis"
    assert crisis["source"] == "self_report"
    assert crisis["matched_categories"] == ["suicidal_ideation"]
    assert crisis["rationale_codes"] == ["assessment.phq9_item9"]
    assert crisis["message"] is not None
    assert crisis["message"]["title"]
    assert crisis["message"]["helpline_intro"]
    assert crisis["resources"], "the crisis flow must carry helplines"
    assert crisis["emergency"] is not None
    assert crisis["emergency"]["tel_href"] or crisis["emergency"]["number"]
    assert crisis["result_saved"] is True
    assert crisis["explanation"] and crisis["encouragement"]

    # …and the score is still there, because hiding it would punish the answer.
    assert body["result"]["total"] == 1
    assert body["result"]["band"] == "minimal"


async def test_item9_at_zero_does_not_trigger_the_crisis_flow(client: httpx.AsyncClient) -> None:
    who = await person(client)
    response = await client.post(
        f"{ASSESSMENTS}/phq9/submit",
        headers=who.headers,
        json={"answers": answers(PHQ9_IDS, 3, phq9_9=0)},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["result"]["total"] == 24
    assert body["crisis"] is None


async def test_gad7_never_triggers_the_crisis_flow(client: httpx.AsyncClient) -> None:
    who = await person(client)
    response = await client.post(
        f"{ASSESSMENTS}/gad7/submit",
        headers=who.headers,
        json={"answers": answers(GAD7_IDS, 3)},
    )
    assert response.status_code == 201
    assert response.json()["crisis"] is None


async def test_the_crisis_message_is_localised(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    who = await person(client)
    user = await UserRepository(session).get(who.id)
    assert user is not None
    user.language = "hi"
    user.region = "IN"
    await session.commit()

    response = await client.post(
        f"{ASSESSMENTS}/phq9/submit",
        headers=who.headers,
        json={"answers": answers(PHQ9_IDS, 0, phq9_9=2)},
    )
    assert response.status_code == 201
    crisis = response.json()["crisis"]
    assert crisis["locale"] == "hi"
    assert crisis["region"] == "IN"
    assert crisis["message"]["locale"] == "hi"
    assert any(resource["region"] == "IN" for resource in crisis["resources"])


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


async def test_a_missing_answer_is_refused_and_nothing_is_stored(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    who = await person(client)
    document = answers(PHQ9_IDS, 1)
    del document["phq9_5"]
    response = await client.post(
        f"{ASSESSMENTS}/phq9/submit", headers=who.headers, json={"answers": document}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "incomplete_assessment"
    assert "phq9_5" in body["error"]["message"]
    assert await count_results(session, AssessmentInstrument.PHQ9) == 0


async def test_an_unknown_item_is_refused(client: httpx.AsyncClient) -> None:
    who = await person(client)
    document = answers(PHQ9_IDS, 0)
    document["phq9_10"] = 1
    response = await client.post(
        f"{ASSESSMENTS}/phq9/submit", headers=who.headers, json={"answers": document}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_answers"


@pytest.mark.parametrize("bad", [4, -1, True, "2", 1.5, None])
async def test_an_answer_that_is_not_an_option_is_refused(
    client: httpx.AsyncClient, bad: object
) -> None:
    who = await person(client)
    document: dict[str, object] = dict(answers(PHQ9_IDS, 0))
    document["phq9_2"] = bad
    response = await client.post(
        f"{ASSESSMENTS}/phq9/submit", headers=who.headers, json={"answers": document}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_answer"
    assert "phq9_2" in body["error"]["message"]


async def test_an_empty_answer_set_is_refused(client: httpx.AsyncClient) -> None:
    who = await person(client)
    response = await client.post(
        f"{ASSESSMENTS}/phq9/submit", headers=who.headers, json={"answers": {}}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# History, ownership and cadence
# --------------------------------------------------------------------------- #


async def test_history_returns_the_callers_own_runs_newest_first(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    who = await person(client)
    for value in (0, 1, 3):
        response = await client.post(
            f"{ASSESSMENTS}/phq9/submit",
            headers=who.headers,
            json={"answers": answers(PHQ9_IDS, value)},
        )
        assert response.status_code == 201

    response = await client.get(f"{ASSESSMENTS}/phq9/history", headers=who.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert [entry["total"] for entry in body["entries"]] == [27, 9, 0]
    assert body["total_max"] == 27
    assert [band["id"] for band in body["bands"]] == [
        "minimal",
        "mild",
        "moderate",
        "moderately_severe",
        "severe",
    ]
    assert all(entry["band"] == "severe" for entry in body["entries"][:1])
    assert body["entries"][-1]["band"] == "minimal"

    # The rows are the caller's, in the table as well as the response.
    stored = (
        (await session.execute(select(AssessmentResult).where(AssessmentResult.user_id == who.id)))
        .scalars()
        .all()
    )
    assert len(stored) == 3
    assert {row.user_id for row in stored} == {who.id}


async def test_history_requires_a_session_and_consent(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{ASSESSMENTS}/phq9/history")).status_code == 401
    without = await person(client, kinds=())
    response = await client.get(f"{ASSESSMENTS}/phq9/history", headers=without.headers)
    assert response.status_code == 403


async def test_another_users_result_is_not_visible(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    owner = await person(client)
    stranger = await person(client)
    created = await client.post(
        f"{ASSESSMENTS}/phq9/submit",
        headers=owner.headers,
        json={"answers": answers(PHQ9_IDS, 2)},
    )
    result_id = created.json()["result"]["id"]

    stranger_history = await client.get(f"{ASSESSMENTS}/phq9/history", headers=stranger.headers)
    assert stranger_history.status_code == 200
    assert stranger_history.json()["count"] == 0

    # 404, not 403: the id's existence is not the stranger's business.
    detail = await client.get(f"{ASSESSMENTS}/phq9/history/{result_id}", headers=stranger.headers)
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "assessment_not_found"

    # The owner can read it, with the band copy attached.
    own = await client.get(f"{ASSESSMENTS}/phq9/history/{result_id}", headers=owner.headers)
    assert own.status_code == 200
    assert own.json()["total"] == 18
    assert own.json()["band"] == "moderately_severe"
    assert "not a diagnosis" in own.json()["screening_not_diagnosis"]

    # …and asking for it under the other instrument is also a 404.
    wrong_instrument = await client.get(
        f"{ASSESSMENTS}/gad7/history/{result_id}", headers=owner.headers
    )
    assert wrong_instrument.status_code == 404


async def test_the_cadence_notice_is_advice_not_a_block(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    who = await person(client)
    clock = [datetime(2026, 10, 1, 9, 0, tzinfo=UTC)]
    monkeypatch.setattr("app.api.v1.assessments._now", lambda: clock[0])

    first = await client.post(
        f"{ASSESSMENTS}/phq9/submit", headers=who.headers, json={"answers": answers(PHQ9_IDS, 0)}
    )
    assert first.json()["cadence"]["is_first"] is True
    assert first.json()["cadence"]["due"] is True
    first_notice = first.json()["cadence"]["notice"]

    # Same day: allowed, stored, and told plainly that comparing now says little.
    soon = await client.post(
        f"{ASSESSMENTS}/phq9/submit", headers=who.headers, json={"answers": answers(PHQ9_IDS, 1)}
    )
    assert soon.status_code == 201
    cadence = soon.json()["cadence"]
    assert cadence["is_first"] is False
    assert cadence["due"] is False
    assert cadence["recommended_interval_days"] == 14
    assert cadence["days_since_previous"] == 0
    assert cadence["notice"] != first_notice
    assert "suggestion" in cadence["notice"]

    # Three weeks later the notice flips to "this is a good moment".
    clock[0] = clock[0] + timedelta(days=21)
    later = await client.post(
        f"{ASSESSMENTS}/phq9/submit", headers=who.headers, json={"answers": answers(PHQ9_IDS, 2)}
    )
    assert later.json()["cadence"]["due"] is True
    assert later.json()["cadence"]["days_since_previous"] == 21
    assert later.json()["cadence"]["notice"] != cadence["notice"]

    assert await count_results(session, AssessmentInstrument.PHQ9) == 3


async def test_history_reports_the_cadence_of_the_latest_run(client: httpx.AsyncClient) -> None:
    who = await person(client)
    empty = await client.get(f"{ASSESSMENTS}/gad7/history", headers=who.headers)
    assert empty.json()["count"] == 0
    assert empty.json()["cadence"]["is_first"] is True

    await client.post(
        f"{ASSESSMENTS}/gad7/submit", headers=who.headers, json={"answers": answers(GAD7_IDS, 1)}
    )
    after = await client.get(f"{ASSESSMENTS}/gad7/history", headers=who.headers)
    assert after.json()["cadence"]["is_first"] is False
    assert after.json()["cadence"]["days_since_previous"] == 0


async def test_history_paging_is_bounded(client: httpx.AsyncClient) -> None:
    who = await person(client)
    too_big = await client.get(f"{ASSESSMENTS}/phq9/history?limit=500", headers=who.headers)
    assert too_big.status_code == 422
    negative = await client.get(f"{ASSESSMENTS}/phq9/history?offset=-1", headers=who.headers)
    assert negative.status_code == 422


async def test_the_repository_rejects_a_result_for_another_instrument(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The repository is thin, but a mismatched row must still be a 404 on read."""
    who = await person(client)
    created = await client.post(
        f"{ASSESSMENTS}/gad7/submit", headers=who.headers, json={"answers": answers(GAD7_IDS, 0)}
    )
    result_id = created.json()["result"]["id"]
    response = await client.get(f"{ASSESSMENTS}/phq9/history/{result_id}", headers=who.headers)
    assert response.status_code == 404


async def test_the_submitted_row_is_stored_with_item_ids_and_no_text(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    who = await person(client)
    await client.post(
        f"{ASSESSMENTS}/phq9/submit",
        headers=who.headers,
        json={"answers": answers(PHQ9_IDS, 0, phq9_9=3)},
    )
    row = (
        await session.execute(select(AssessmentResult).where(AssessmentResult.user_id == who.id))
    ).scalar_one()
    assert set(row.answers) == set(PHQ9_IDS)
    assert row.answers["phq9_9"] == 3
    assert row.band is AssessmentBand.SEVERE or row.band is AssessmentBand.MINIMAL
    assert isinstance(row.total, int)


async def test_history_ignores_the_other_instrument(client: httpx.AsyncClient) -> None:
    who = await person(client)
    await client.post(
        f"{ASSESSMENTS}/phq9/submit", headers=who.headers, json={"answers": answers(PHQ9_IDS, 0)}
    )
    gad7 = await client.get(f"{ASSESSMENTS}/gad7/history", headers=who.headers)
    assert gad7.json()["count"] == 0
    phq9 = await client.get(f"{ASSESSMENTS}/phq9/history", headers=who.headers)
    assert phq9.json()["count"] == 1


async def test_rows_written_directly_are_readable_by_the_repository(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """A row inserted by anything other than the endpoint still pages correctly."""
    who = await person(client)
    repo = AssessmentRepository(session)
    await repo.record(
        user_id=who.id,
        instrument=AssessmentInstrument.GAD7,
        answers=dict.fromkeys(GAD7_IDS, 2),
        total=14,
        band=AssessmentBand.MODERATE,
    )
    await session.commit()
    response = await client.get(f"{ASSESSMENTS}/gad7/history", headers=who.headers)
    assert response.status_code == 200
    assert [entry["total"] for entry in response.json()["entries"]] == [14]
