"""Crisis endpoints: public, complete, and matching the shipped content.

Two endpoints are covered here end to end, because both are safety-critical and
both are public:

* ``GET /crisis/resources`` — with and without ``?region=``, including the
  unknown-region fallback that must never 404 or return an empty list;
* ``POST /crisis/assess`` — the rules engine behind HTTP, including the privacy
  property that matters most: the input text is never echoed back.

The Day 5 version of this file asserted the old ``phone``/``sms`` shape. Every
one of those properties is still asserted here, against the new contract.
"""

from __future__ import annotations

import httpx
import pytest

from app.content.crisis import FALLBACK_REGION, load_helplines
from app.content.i18n import available_locales

RESOURCES = "/api/v1/crisis/resources"
ASSESS = "/api/v1/crisis/assess"


# --------------------------------------------------------------------------- #
# GET /crisis/resources                                                        #
# --------------------------------------------------------------------------- #


async def test_resources_are_public(client: httpx.AsyncClient) -> None:
    """No token, no consent, no account: the endpoint answers anyway.

    This is the safety property of the endpoint — an auth wall in front of a
    helpline list is a bug, not a feature.
    """
    response = await client.get(RESOURCES)

    assert response.status_code == 200
    body = response.json()
    assert body["last_verified"] == "2026-10-09"
    assert len(body["resources"]) >= 1


async def test_resources_match_the_shipped_file(client: httpx.AsyncClient) -> None:
    content = load_helplines()

    body = (await client.get(RESOURCES)).json()

    assert body["disclaimer"] == content.disclaimer
    assert body["known_regions"] == list(content.known_regions())
    assert body["region"] is None
    assert body["fallback_used"] is False
    # Without a region the whole file is served, in display order.
    expected = sorted(content.resources, key=lambda item: (item.priority, item.id))
    assert [resource["id"] for resource in body["resources"]] == [item.id for item in expected]
    assert [resource["priority"] for resource in body["resources"]] == [
        item.priority for item in expected
    ]


async def test_every_resource_is_reachable(client: httpx.AsyncClient) -> None:
    body = (await client.get(RESOURCES)).json()

    for resource in body["resources"]:
        assert resource["number"] is not None or resource["url"]
        assert resource["hours"] != ""
        assert resource["description"] != ""
        assert resource["source_url"].startswith("http")
        assert resource["last_verified"]
        # Provenance is per entry, and unverified entries must say why.
        if resource["needs_verification"]:
            assert (resource["verification_note"] or "").strip()


async def test_dialling_links_come_down_the_wire(client: httpx.AsyncClient) -> None:
    """A client must not have to guess how to build a tel:/sms: URL."""
    body = (await client.get(RESOURCES)).json()

    call_lines = [item for item in body["resources"] if item["type"] == "call" and item["number"]]
    text_lines = [item for item in body["resources"] if item["type"] == "text" and item["number"]]
    assert call_lines and text_lines

    assert call_lines[0]["tel_href"].startswith("tel:")
    assert call_lines[0]["sms_href"] is None
    assert text_lines[0]["sms_href"].startswith("sms:")
    assert text_lines[0]["tel_href"] is None
    # Nothing but diallable characters survives into the href.
    for item in (*call_lines, *text_lines):
        href = item["tel_href"] or item["sms_href"]
        assert set(href.split(":", 1)[1]) <= set("0123456789+")


@pytest.mark.parametrize("region", ["IN", "US", "GB", "AU", "CA"])
async def test_a_region_gets_its_own_list_and_emergency_number(
    client: httpx.AsyncClient, region: str
) -> None:
    content = load_helplines()

    body = (await client.get(RESOURCES, params={"region": region})).json()

    assert body["region"] == region
    assert body["requested_region"] == region
    assert body["fallback_used"] is False
    assert body["emergency"]["region"] == region
    assert body["emergency"]["kind"] == "emergency"
    assert body["emergency"]["number"]
    assert body["resources"][0]["kind"] == "emergency"
    assert [item["id"] for item in body["resources"]] == [
        item.id for item in content.resources_for(region)
    ]
    # A region's own emergency number replaces the DEFAULT one; it is never both.
    assert sum(1 for item in body["resources"] if item["kind"] == "emergency") == 1


async def test_region_parameter_is_case_insensitive(client: httpx.AsyncClient) -> None:
    lower = (await client.get(RESOURCES, params={"region": "in"})).json()
    upper = (await client.get(RESOURCES, params={"region": "IN"})).json()

    assert lower["region"] == "IN"
    assert lower == upper


async def test_an_unknown_region_falls_back_rather_than_failing(
    client: httpx.AsyncClient,
) -> None:
    """Someone who typed the wrong country code still gets a number to dial."""
    body = (await client.get(RESOURCES, params={"region": "ZZ"})).json()

    assert body["region"] == FALLBACK_REGION
    assert body["requested_region"] == "ZZ"
    assert body["fallback_used"] is True
    assert body["resources"]
    assert body["emergency"]["number"]


async def test_a_region_query_that_is_too_short_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(RESOURCES, params={"region": "I"})

    assert response.status_code == 422


async def test_a_signed_in_user_gets_the_same_list(client: httpx.AsyncClient) -> None:
    """Signing in must not change the answer."""
    guest = (await client.post("/api/v1/auth/guest")).json()
    headers = {"Authorization": f"Bearer {guest['access_token']}"}

    anonymous = (await client.get(RESOURCES)).json()
    authenticated = (await client.get(RESOURCES, headers=headers)).json()

    assert authenticated == anonymous


async def test_crisis_is_not_rate_limited_like_auth_endpoint(
    client_factory: object,
) -> None:
    """The global limiter applies, not the strict auth budget."""
    factory = client_factory
    assert callable(factory)
    strict = factory(rate_limit_auth_per_minute=1, rate_limit_global_per_minute=100)
    try:
        for _ in range(5):
            response = await strict.get(RESOURCES)
            assert response.status_code == 200
    finally:
        await strict.aclose()


# --------------------------------------------------------------------------- #
# POST /crisis/assess                                                          #
# --------------------------------------------------------------------------- #


async def test_assess_is_public_and_deterministic(client: httpx.AsyncClient) -> None:
    """No auth, and the same text always gets the same answer."""
    payload = {"text": "I want to die", "region": "IN"}

    first = await client.post(ASSESS, json=payload)
    second = await client.post(ASSESS, json=payload)

    assert first.status_code == 200
    assert first.json() == second.json()


async def test_assess_high_risk_blocks_the_llm_and_returns_the_crisis_card(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.post(ASSESS, json={"text": "I want to die", "region": "IN"})).json()

    assert body["level"] == "high"
    assert body["stored_level"] == "crisis"
    assert body["matched_categories"] == ["suicidal_ideation"]
    assert "si.want_to_die" in body["rationale_codes"]
    assert body["policy"]["allow_llm"] is False
    assert body["policy"]["deterministic_reply"] is True
    assert body["policy"]["template_id"] == "crisis.high"
    assert "block_llm" in body["policy"]["actions"]
    assert body["policy"]["record_event"] is True
    assert body["crisis"]["title"]
    assert body["crisis"]["emergency_instruction"]
    assert "112" in body["crisis"]["emergency_instruction"]
    assert body["crisis"]["safety_steps"]
    # Helplines come with the message, emergency first, already diallable.
    assert body["region"] == "IN"
    assert body["resources"][0]["kind"] == "emergency"
    assert body["resources"][0]["tel_href"] == "tel:112"


async def test_assess_imminent_risk_gets_the_urgent_template(
    client: httpx.AsyncClient,
) -> None:
    body = (
        await client.post(
            ASSESS, json={"text": "I just took a whole bottle of pills", "region": "US"}
        )
    ).json()

    assert body["level"] == "imminent"
    assert body["matched_categories"] == ["acute_medical"]
    assert body["policy"]["template_id"] == "crisis.imminent"
    assert body["policy"]["show_emergency_instruction"] is True
    assert "911" in body["crisis"]["emergency_instruction"]
    assert body["emergency"]["number"] == "911"


async def test_assess_shapes_the_reply_for_a_message_about_somebody_else(
    client: httpx.AsyncClient,
) -> None:
    body = (
        await client.post(ASSESS, json={"text": "my friend says she wants to die", "region": "GB"})
    ).json()

    assert body["level"] == "high"
    assert body["about_someone_else"] is True
    assert body["context"]["third_person"] is True
    assert body["policy"]["template_id"] == "crisis.about_someone_else"
    assert body["crisis"]["safety_steps"]


async def test_assess_does_not_treat_a_victim_as_a_bystander(
    client: httpx.AsyncClient,
) -> None:
    """ "My husband threatens to kill me" is about somebody else *and* about the
    speaker — and the speaker is the one who needs the crisis card."""
    body = (
        await client.post(ASSESS, json={"text": "my husband threatens to kill me", "region": "IN"})
    ).json()

    assert body["level"] == "high"
    assert body["context"]["first_person"] is True
    assert body["about_someone_else"] is False
    assert body["policy"]["template_id"] == "crisis.high"


async def test_assess_negated_and_figurative_text_do_not_get_a_crisis_card(
    client: httpx.AsyncClient,
) -> None:
    negated = (await client.post(ASSESS, json={"text": "I don't want to die"})).json()
    figurative = (await client.post(ASSESS, json={"text": "this traffic is killing me"})).json()

    assert negated["level"] == "low"
    assert negated["policy"]["allow_llm"] is True
    assert negated["policy"]["template_id"] == "check_in.low"
    assert negated["resources"] == []

    assert figurative["level"] == "none"
    assert figurative["crisis"] is None
    assert figurative["resources"] == []
    assert "ctx.figurative" in figurative["rationale_codes"]


@pytest.mark.parametrize("locale", sorted(available_locales()))
async def test_assess_localises_the_crisis_message(client: httpx.AsyncClient, locale: str) -> None:
    body = (
        await client.post(ASSESS, json={"text": "I want to die", "region": "IN", "locale": locale})
    ).json()

    assert body["locale"] == locale
    assert body["locale_fallback_used"] is False
    assert body["crisis"]["locale"] == locale
    assert body["crisis"]["title"].strip()


async def test_assess_accepts_a_bcp47_tag_and_falls_back_on_an_unknown_locale(
    client: httpx.AsyncClient,
) -> None:
    tagged = (await client.post(ASSESS, json={"text": "I want to die", "locale": "hi-IN"})).json()
    unknown = (await client.post(ASSESS, json={"text": "I want to die", "locale": "xx"})).json()

    assert tagged["locale"] == "hi"
    assert tagged["locale_fallback_used"] is False
    assert unknown["locale"] == "en"
    assert unknown["locale_fallback_used"] is True


async def test_assess_never_echoes_the_input_text(client: httpx.AsyncClient) -> None:
    """The privacy property: the response is metadata and pre-written copy only."""
    secret = "my bank password is hunter2 and I want to die"

    response = await client.post(ASSESS, json={"text": secret, "region": "IN"})
    raw = response.text

    assert response.status_code == 200
    assert "hunter2" not in raw
    assert "bank password" not in raw
    body = response.json()
    assert body["level"] == "high"
    # Rationale codes are pattern ids, not fragments of the message.
    for code in body["rationale_codes"]:
        assert " " not in code
        assert code.islower() or code.startswith(("ctx.", "input."))


async def test_assess_rejects_blank_and_absurd_input(client: httpx.AsyncClient) -> None:
    from app.api.v1.crisis import MAX_REQUEST_CHARS

    blank = await client.post(ASSESS, json={"text": "   "})
    empty = await client.post(ASSESS, json={"text": ""})
    huge = await client.post(ASSESS, json={"text": "a" * (MAX_REQUEST_CHARS + 1)})

    assert blank.status_code == 422
    assert empty.status_code == 422
    assert huge.status_code == 422


async def test_assess_reports_a_truncated_message_rather_than_dropping_it(
    client: httpx.AsyncClient,
) -> None:
    """A long entry is still assessed on what fit, and says so."""
    from app.services.safety.normalise import DEFAULT_MAX_INPUT_CHARS

    text = "I want to die " + ("and nothing helps " * 400)
    assert len(text) > DEFAULT_MAX_INPUT_CHARS

    body = (await client.post(ASSESS, json={"text": text})).json()

    assert body["level"] == "high"
    assert body["context"]["truncated"] is True
    assert "input.truncated" in body["rationale_codes"]
