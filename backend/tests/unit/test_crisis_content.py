"""The helpline content module: what it accepts, and what it refuses."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest

from app.content.crisis import load_helplines, parse_helplines


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "last_verified": "2026-10-01",
        "source": "test source",
        "disclaimer": "test disclaimer",
        "resources": [
            {
                "id": "test-line",
                "region": "US",
                "name": "Test Helpline",
                "phone": "988",
                "sms": None,
                "url": "https://example.test",
                "hours": "24/7",
                "description": "A helpline for tests.",
                "priority": 10,
            }
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
    assert len(content.resources) >= 1
    assert content.disclaimer != ""
    for resource in content.resources:
        assert resource.id and resource.name and resource.region
        assert resource.phone is not None or resource.sms is not None or resource.url is not None


def test_shipped_file_is_sorted_by_priority() -> None:
    content = load_helplines()

    priorities = [resource.priority for resource in content.resources]
    assert priorities == sorted(priorities)


# --------------------------------------------------------------------------- #
# Validation                                                                   #
# --------------------------------------------------------------------------- #


def test_minimal_payload_parses() -> None:
    content = parse_helplines(_payload())

    assert content.resources[0].phone == "988"
    # Pydantic normalises the URL, so it comes back with a trailing slash.
    assert str(content.resources[0].url) == "https://example.test/"


def test_rejects_duplicate_ids() -> None:
    payload = _payload()
    payload["resources"].append(copy.deepcopy(payload["resources"][0]))

    with pytest.raises(ValueError, match="duplicate resource ids"):
        parse_helplines(payload)


def test_rejects_a_resource_with_no_way_to_make_contact() -> None:
    payload = _payload()
    payload["resources"][0]["phone"] = None
    payload["resources"][0]["url"] = None

    with pytest.raises(ValueError, match="no way to make contact"):
        parse_helplines(payload)


def test_rejects_an_empty_resource_list() -> None:
    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(_payload(resources=[]))


def test_rejects_an_undialable_number() -> None:
    payload = _payload()
    payload["resources"][0]["phone"] = "call me maybe"

    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(payload)


def test_rejects_a_url_that_is_not_a_url() -> None:
    payload = _payload()
    payload["resources"][0]["url"] = "not-a-url"

    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(payload)


def test_rejects_a_missing_verification_date() -> None:
    payload = _payload()
    del payload["last_verified"]

    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(payload)


def test_rejects_an_out_of_range_priority() -> None:
    payload = _payload()
    payload["resources"][0]["priority"] = -1

    with pytest.raises(ValueError, match="failed validation"):
        parse_helplines(payload)
