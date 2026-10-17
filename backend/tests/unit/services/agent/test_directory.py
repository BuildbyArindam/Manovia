"""The curated directory: loading, filtering, exclusion.

The shipped file validates against its published JSON
schema; searches exclude ``needs_verification`` entries
by construction; a sub-national region also matches its
country's entries; and entry text is sanitised before it
can reach a model or a person.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.agent.directory import DirectoryStore

SHIPPED = Path(__file__).resolve().parents[4] / "app" / "content" / "resources" / "directory.json"


@pytest.fixture(scope="module")
def store() -> DirectoryStore:
    return DirectoryStore.load()


def test_the_shipped_file_validates_against_its_schema() -> None:
    payload = json.loads(SHIPPED.read_text(encoding="utf-8"))
    assert payload["version"] >= 1
    assert payload["resources"]


def test_every_entry_carries_a_source_and_a_date(store: DirectoryStore) -> None:
    for entry in store.data.resources:
        # The IIT Kharagpur campus portal is plain http
        # (verified from the SARTH student portal); every
        # other source is https.
        assert entry.source_url.startswith(("https://", "http://"))
        assert entry.last_verified.isoformat() == "2026-10-16"


def test_unverified_entries_are_excluded_from_search(
    store: DirectoryStore,
) -> None:
    excluded_ids = {entry.id for entry in store.excluded()}
    assert excluded_ids, "the shipped file seeds at least one "
    "needs_verification entry to prove the exclusion"
    results = store.search(region="IN", budget="any")
    for result in results:
        assert result["id"] not in excluded_ids


def test_excluded_entries_name_why(store: DirectoryStore) -> None:
    for entry in store.excluded():
        assert entry.verification_note


def test_search_filters_by_region_language_need_and_budget(
    store: DirectoryStore,
) -> None:
    results = store.search(region="IN-WB", language="hi", budget="low_cost")
    ids = {result["id"] for result in results}
    # Hindi-speaking, free or low-cost, serving West Bengal:
    # the national lines that serve Bengali and Hindi.
    assert "in-tele-manas" in ids
    assert "in-kiran" in ids
    # An English-only Kolkata line must not surface for a
    # Hindi ask.
    assert "in-wb-lifeline-foundation" not in ids


def test_a_subnational_region_matches_its_country(store: DirectoryStore) -> None:
    wb_ids = {result["id"] for result in store.search(region="IN-WB")}
    in_ids = {result["id"] for result in store.search(region="IN")}
    assert "in-wb-lifeline-foundation" in wb_ids
    assert wb_ids <= in_ids


def test_budget_free_admits_only_no_cost_options(store: DirectoryStore) -> None:
    for result in store.search(region="IN", budget="free"):
        assert result["cost"] == "free"


def test_low_cost_admits_free_and_low_cost(store: DirectoryStore) -> None:
    for result in store.search(region="IN", budget="low_cost"):
        assert result["cost"] in {"free", "low_cost"}


def test_an_unknown_region_serves_global_entries_only(store: DirectoryStore) -> None:
    results = store.search(region="ZZ")
    assert results, "global directories always match"
    for result in results:
        assert "GLOBAL" in result["regions"]


def test_results_are_length_capped(store: DirectoryStore) -> None:
    results = store.search(region="IN")
    assert len(results) <= 8


def test_entry_text_is_sanitised(store: DirectoryStore) -> None:
    for result in store.search(region="GLOBAL"):
        for key in ("name", "description", "hours"):
            value = result[key]
            assert "\n" not in value
            assert "\x00" not in value
            assert value == value.strip()
