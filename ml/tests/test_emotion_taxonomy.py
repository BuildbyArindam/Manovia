"""The label-mapping table: the decisions, checked rather than asserted.

Every test here is about a claim made in a docstring or a model card. If a claim
stops being true — a weight changes, a label appears, ``loneliness`` becomes
reachable from GoEmotions — the suite fails instead of the report quietly
disagreeing with the code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from manovia_ml.training.emotion.taxonomy import (
    EMONOBA_MAP,
    EMOTIONS,
    GOEMOTIONS_MAP,
    NEUTRAL,
    NEUTRAL_THRESHOLD,
    SCHEMES,
    mapped_targets,
    source_label_map,
    to_target_vector,
    unreachable_targets,
)

#: The 27 GoEmotions labels plus `neutral`, transcribed from the corpus card.
GOEMOTIONS_LABELS = (
    "admiration",
    "amusement",
    "anger",
    "annoyance",
    "approval",
    "caring",
    "confusion",
    "curiosity",
    "desire",
    "disappointment",
    "disapproval",
    "disgust",
    "embarrassment",
    "excitement",
    "fear",
    "gratitude",
    "grief",
    "joy",
    "love",
    "nervousness",
    "optimism",
    "pride",
    "realization",
    "relief",
    "remorse",
    "sadness",
    "surprise",
    "neutral",
)

BACKEND_BASE = (
    Path(__file__).resolve().parents[2] / "backend" / "app" / "services" / "nlp" / "base.py"
)


def test_taxonomy_matches_the_product() -> None:
    """Manovia's nine labels, in the product's order — parsed from the backend file.

    Parsed rather than imported so `ml/` keeps its independence (`ml/` must be
    installable without the backend), and so this test still runs in a checkout
    where the backend is not on `sys.path`.
    """

    source = BACKEND_BASE.read_text(encoding="utf-8")
    block = re.search(r"EMOTIONS: tuple\[str, \.\.\.\] = \((.*?)\)", source, re.S)
    assert block is not None, f"EMOTIONS not found in {BACKEND_BASE}"
    labels = tuple(re.findall(r'"([a-z]+)"', block.group(1)))
    assert labels == EMOTIONS


def test_every_source_label_is_mapped_or_explicitly_dropped() -> None:
    """No silence: a scheme that grows a label fails here instead of losing it."""

    assert set(GOEMOTIONS_MAP.labels) == set(GOEMOTIONS_LABELS)
    assert set(EMONOBA_MAP.labels) == {"anger", "fear", "joy", "love", "sadness", "surprise"}


def test_targets_are_in_the_taxonomy_and_weights_are_possible() -> None:
    for scheme in SCHEMES.values():
        for item in scheme.mappings:
            for target, weight in item.targets:
                assert target in EMOTIONS, f"{scheme.name}/{item.source} targets {target}"
                assert 0.0 < weight <= 1.0, f"{scheme.name}/{item.source} weight {weight}"
            assert item.rationale, f"{scheme.name}/{item.source} has no rationale"


def test_goemotions_cannot_express_loneliness() -> None:
    """A fact about the corpus, not a bug in the table — and it is documented."""

    assert unreachable_targets(GOEMOTIONS_MAP) == ("loneliness",)
    assert "loneliness" not in mapped_targets("goemotions")
    # EmoNoBa has no loneliness class either; neutral is derived for every
    # scheme, which is why `unreachable_targets` never lists it.
    # EmoNoBa has four labels, so four of the nine emotions have no supervision
    # from it at all — reported in taxonomy order.
    assert unreachable_targets(EMONOBA_MAP) == ("anxiety", "shame", "loneliness", "calm")
    assert NEUTRAL not in unreachable_targets(GOEMOTIONS_MAP)


def test_emonoba_drops_love_and_surprise_per_the_catalogue_condition() -> None:
    table = source_label_map("emonoba")
    assert table["love"] == ()
    assert table["surprise"] == ()
    assert table["anger"] == (("anger", 1.0),)


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (["joy"], {"joy": 1.0}),
        (["nervousness"], {"anxiety": 1.0}),
        (["embarrassment"], {"shame": 1.0}),
        (["remorse"], {"shame": 0.9}),
        (["grief"], {"sadness": 1.0}),
        (["disgust"], {"anger": 0.8}),
        (["relief"], {"calm": 0.9}),
        # Two labels for the same target: the maximum wins, exactly like the
        # product's own LABEL_MAP, so {sadness, grief} is not "twice as sad".
        (["sadness", "grief"], {"sadness": 1.0}),
        (["approval", "admiration"], {"joy": 0.6}),
        # Dropped labels produce nothing at all.
        (["surprise"], {}),
        (["curiosity", "realization"], {}),
    ],
)
def test_folding_source_labels(labels: list[str], expected: dict[str, float]) -> None:
    from manovia_ml.training.emotion.taxonomy import target_weights

    assert target_weights(labels, scheme="goemotions") == expected


def test_neutral_is_derived_not_copied() -> None:
    vector = dict(zip(EMOTIONS, to_target_vector(["neutral"], scheme="goemotions"), strict=True))
    assert vector[NEUTRAL] == 1.0
    assert all(value == 0.0 for label, value in vector.items() if label != NEUTRAL)

    # A label whose weight is below the neutral cut leaves the row neutral: the
    # documented consequence of the 0.4/0.3 weights on confusion/realization.
    weak = dict(zip(EMOTIONS, to_target_vector(["confusion"], scheme="goemotions"), strict=True))
    assert weak[NEUTRAL] == 1.0

    # ...while a mapped emotion above the cut clears it.
    strong = dict(zip(EMOTIONS, to_target_vector(["grief"], scheme="goemotions"), strict=True))
    assert strong[NEUTRAL] == 0.0
    assert strong["sadness"] == 1.0

    # The invariant that matters downstream: neutral never co-occurs.
    for labels in (["joy"], ["anger", "disgust"], ["grief", "remorse"], ["relief"]):
        row = dict(zip(EMOTIONS, to_target_vector(labels, scheme="goemotions"), strict=True))
        others = [
            value for label, value in row.items() if label != NEUTRAL and value >= NEUTRAL_THRESHOLD
        ]
        assert (row[NEUTRAL] == 1.0) is (not others), labels


def test_drop_report_counts_what_the_conversion_loses() -> None:
    from manovia_ml.training.emotion.taxonomy import drop_report

    report = drop_report(
        [
            {"labels": ["joy"]},
            {"labels": ["surprise"]},
            {"labels": ["curiosity"]},
        ],
        scheme="goemotions",
    )
    assert report["dropped:surprise"] == 1
    assert report["dropped:curiosity"] == 1
    assert report["rows_without_a_target"] == 2
    # `love` is a *mapped* label for GoEmotions (joy 0.8) and a dropped one for
    # EmoNoBa, per the catalogue's condition on that corpus.
    assert drop_report([{"labels": ["love"]}], scheme="goemotions") == {}
    assert drop_report([{"labels": ["love"]}], scheme="emonoba") == {
        "dropped:love": 1,
        "rows_without_a_target": 1,
    }
