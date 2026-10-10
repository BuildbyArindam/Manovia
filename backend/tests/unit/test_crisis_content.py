"""The helpline content module: what it accepts, and what it refuses.

The contract changed shape in Day 8 (``phone``/``sms`` → ``number``/``type`` +
``kind``, plus per-resource provenance), so these tests assert the *new*
contract and the invariants that come with it. Every assertion the Day 5 version
made still holds here in a stronger form: the shipped file parses, is servable,
is sorted, and no entry may be unreachable.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any, Final

import pytest

from app.content.crisis import FALLBACK_REGION, load_helplines, parse_helplines


def _resource(**overrides: Any) -> dict[str, Any]:
    """One valid v2 resource. Callers override the fields they mean to break."""
    resource: dict[str, Any] = {
        "id": "test-line",
        "region": "US",
        "kind": "crisis_line",
        "name": "Test Helpline",
        "number": "988",
        "type": "call",
        "hours": "24/7",
        "languages": ["en"],
        "description": "A helpline used by the tests.",
        "url": "https://example.test",
        "source_url": "https://example.test/source",
        "last_verified": "2026-10-01",
        "needs_verification": False,
        "priority": 10,
    }
    resource.update(overrides)
    return resource


#: Index of the ordinary (non-emergency) line in :func:`_payload`. The emergency
#: entries exist so every negative test breaks exactly one invariant: without a
#: US emergency number the "no emergency number" check fires before the one under
#: test.
CRISIS_LINE: Final = 1


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 2,
        "last_verified": "2026-10-01",
        "source": "A source long enough to pass validation.",
        "disclaimer": "A disclaimer long enough to pass validation.",
        "regions": ["US", FALLBACK_REGION],
        "resources": [
            _resource(
                id="us-emergency",
                kind="emergency",
                name="US emergency number",
                number="911",
                priority=1,
            ),
            _resource(),
            _resource(
                id="default-emergency",
                region=FALLBACK_REGION,
                kind="emergency",
                name="International emergency number",
                number="112",
                priority=1,
            ),
        ],
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# The shipped file                                                             #
# --------------------------------------------------------------------------- #


def test_shipped_file_parses_and_is_servable() -> None:
    content = load_helplines()

    assert content.last_verified == date(2026, 10, 9)
    assert content.version == 2
    assert len(content.resources) >= 1
    assert content.disclaimer != ""
    for resource in content.resources:
        assert resource.id and resource.name and resource.region
        # Reachable: a number to dial/text, or a URL to open. A directory entry
        # is the only kind allowed to have no number.
        assert resource.number is not None or resource.url is not None
        if resource.kind == "directory":
            assert resource.number is None


def test_every_region_is_served_in_priority_order() -> None:
    """The order a person actually sees: emergency first, then by priority.

    The v2 file groups entries by region and sorts within each group, so the
    global list is not monotonic — the *served* list is, and that is the one a
    client renders.
    """
    content = load_helplines()

    for region in content.known_regions():
        served = content.resources_for(region)
        keys = [(resource.priority, resource.id) for resource in served]
        assert keys == sorted(keys), region
        assert served[0].kind == "emergency", region

    # And within each region block in the file itself, the same order holds.
    for region in content.known_regions():
        block = [resource.priority for resource in content.resources if resource.region == region]
        assert block == sorted(block), region


def test_every_shipped_resource_carries_its_provenance() -> None:
    """AGENTS.md safety rule 6: a source URL and a verification date, always."""
    content = load_helplines()

    for resource in content.resources:
        assert resource.source_url is not None
        assert str(resource.source_url).startswith("http")
        assert resource.last_verified <= content.last_verified
        # Nothing unverified may be published without saying what is unconfirmed.
        if resource.needs_verification:
            assert (resource.verification_note or "").strip()


def test_every_declared_region_has_a_dialable_emergency_number() -> None:
    content = load_helplines()

    for region in content.known_regions():
        emergency = content.emergency_for(region)
        assert emergency is not None, region
        assert emergency.kind == "emergency"
        assert (emergency.number or "").strip()


def test_dialling_links_are_derived_from_the_number() -> None:
    content = load_helplines()

    for resource in content.resources:
        if resource.type == "call" and resource.number:
            assert resource.tel_href == f"tel:{resource.number.replace(' ', '')}"
            assert resource.sms_href is None
        elif resource.type == "text" and resource.number:
            assert resource.sms_href is not None
            assert resource.sms_href.startswith("sms:")
        else:
            assert resource.tel_href is None


# --------------------------------------------------------------------------- #
# Region resolution                                                            #
# --------------------------------------------------------------------------- #


def test_resources_for_a_region_include_the_globals_but_not_a_second_emergency() -> None:
    content = load_helplines()

    served = content.resources_for("IN")

    assert {resource.region for resource in served} == {"IN", FALLBACK_REGION}
    emergencies = [resource for resource in served if resource.kind == "emergency"]
    assert len(emergencies) == 1
    assert emergencies[0].region == "IN"


def test_an_unknown_region_is_served_the_default_set() -> None:
    content = load_helplines()

    resolved, fallback_used = content.resolve_region("ZZ")

    assert (resolved, fallback_used) == (FALLBACK_REGION, True)
    assert content.resources_for("ZZ") == content.resources_for(FALLBACK_REGION)
    assert content.emergency_for("ZZ") is not None


# --------------------------------------------------------------------------- #
# Validation                                                                   #
# --------------------------------------------------------------------------- #


def test_minimal_payload_parses() -> None:
    content = parse_helplines(_payload())

    line = content.resources[CRISIS_LINE]
    assert line.number == "988"
    assert line.type == "call"
    # Pydantic normalises the URL, so it comes back with a trailing slash.
    assert str(line.url) == "https://example.test/"


def test_rejects_duplicate_ids() -> None:
    payload = _payload()
    payload["resources"].append(copy.deepcopy(payload["resources"][0]))

    with pytest.raises(ValueError, match="duplicate resource ids"):
        parse_helplines(payload)


def test_rejects_a_resource_with_no_way_to_make_contact() -> None:
    payload = _payload()
    payload["resources"][CRISIS_LINE]["number"] = None
    payload["resources"][CRISIS_LINE]["url"] = None

    with pytest.raises(ValueError, match="no way to make contact"):
        parse_helplines(payload)


def test_rejects_a_missing_default_region() -> None:
    payload = _payload(regions=["US"])

    with pytest.raises(ValueError, match="must declare a DEFAULT region"):
        parse_helplines(payload)


def test_rejects_a_resource_in_an_undeclared_region() -> None:
    payload = _payload()
    payload["resources"].append(_resource(id="gb-line", region="GB", number="116123"))

    with pytest.raises(ValueError, match="unknown regions"):
        parse_helplines(payload)


def test_rejects_a_region_with_no_emergency_number() -> None:
    payload = _payload()
    payload["resources"] = [
        resource for resource in payload["resources"] if resource["kind"] != "emergency"
    ]

    with pytest.raises(ValueError, match="no emergency number"):
        parse_helplines(payload)


def test_rejects_an_unverified_resource_that_explains_nothing() -> None:
    payload = _payload()
    payload["resources"][0]["needs_verification"] = True

    with pytest.raises(ValueError, match="explains nothing"):
        parse_helplines(payload)


def test_rejects_an_empty_resource_list() -> None:
    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(_payload(resources=[]))


def test_rejects_an_undialable_number() -> None:
    payload = _payload()
    payload["resources"][0]["number"] = "call me maybe"

    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(payload)


def test_rejects_a_url_that_is_not_a_url() -> None:
    payload = _payload()
    payload["resources"][0]["url"] = "not-a-url"

    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(payload)


def test_rejects_a_missing_source_url() -> None:
    """The provenance field is the one a reviewer cannot do without."""
    payload = _payload()
    del payload["resources"][0]["source_url"]

    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(payload)


def test_rejects_a_missing_verification_date() -> None:
    payload = _payload()
    del payload["last_verified"]

    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(payload)


def test_rejects_a_missing_per_resource_verification_date() -> None:
    payload = _payload()
    del payload["resources"][0]["last_verified"]

    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(payload)


def test_rejects_an_out_of_range_priority() -> None:
    payload = _payload()
    payload["resources"][0]["priority"] = -1

    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(payload)


def test_rejects_blank_languages() -> None:
    payload = _payload()
    payload["resources"][0]["languages"] = ["en", "  "]

    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(payload)
