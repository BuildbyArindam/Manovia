"""The GoEmotions conversion, tested offline.

Nothing here touches the network: `convert` is a pure function over records, and
the download path is behind an explicit flag that CI never passes (the notebook
is where the corpus is actually fetched).
"""

from __future__ import annotations

from typing import Any

import pytest

from manovia_ml.data.goemotions import (
    GOEMOTIONS_LABELS,
    LABEL_MAP,
    PRIORITY,
    conversion_report,
    convert,
)


def record(text: str, labels: list[str] | list[int], **extra: Any) -> dict[str, Any]:
    return {"text": text, "labels": labels, **extra}


def test_all_mapped_labels_are_known_emotions() -> None:
    assert set(LABEL_MAP.values()) <= {
        "joy",
        "sadness",
        "anger",
        "fear",
        "anxiety",
        "shame",
        "calm",
        "neutral",
    }
    assert set(PRIORITY) == set(LABEL_MAP.values())


def test_every_goemotions_label_is_either_mapped_or_deliberately_dropped() -> None:
    """Silence about a label is how a mapping rots when the dataset is re-released."""

    dropped = set(GOEMOTIONS_LABELS) - set(LABEL_MAP)
    assert dropped == {"curiosity", "realization", "surprise"}
    for label in LABEL_MAP:
        assert label in GOEMOTIONS_LABELS


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (["joy"], "joy"),
        (["amusement"], "joy"),
        (["relief"], "calm"),
        (["nervousness"], "anxiety"),
        (["embarrassment"], "shame"),
        (["grief"], "sadness"),
        (["neutral"], "neutral"),
    ],
)
def test_single_labels_map_as_documented(source: list[str], expected: str) -> None:
    rows = list(convert([record("hello", source)]))

    assert rows[0]["label"] == expected
    assert rows[0]["lang"] == "en"
    assert rows[0]["source"] == "goemotions"
    assert rows[0]["source_labels"] == source


def test_multi_label_rows_collapse_by_priority() -> None:
    """`sadness` beats a co-occurring `neutral`, and the source labels are kept."""

    rows = list(convert([record("mixed", ["neutral", "sadness", "annoyance"])]))

    assert rows[0]["label"] == "sadness"
    assert rows[0]["source_labels"] == ["annoyance", "neutral", "sadness"]


def test_unmappable_rows_are_dropped_by_default() -> None:
    rows = list(convert([record("oh", ["surprise"]), record("joyful", ["joy"])]))

    assert [row["label"] for row in rows] == ["joy"]


def test_unmappable_rows_can_be_kept_for_inspection() -> None:
    rows = list(convert([record("oh", ["surprise"])], keep_unmapped=True))

    assert rows[0]["label"] == "unmapped"


def test_integer_labels_resolve_through_the_published_order() -> None:
    index = GOEMOTIONS_LABELS.index("grief")
    rows = list(convert([record("loss", [index])]))

    assert rows[0]["label"] == "sadness"
    assert rows[0]["source_labels"] == ["grief"]


def test_neutral_rows_can_be_dropped() -> None:
    rows = list(convert([record("a", ["neutral"]), record("b", ["joy"])], drop_neutral=True))

    assert [row["label"] for row in rows] == ["joy"]


def test_empty_text_is_skipped() -> None:
    rows = list(convert([record("   ", ["joy"]), record("real", ["joy"])]))

    assert len(rows) == 1


def test_ids_are_stable_and_prefixed_by_split() -> None:
    first = list(convert([record("hello", ["joy"], id="abc")], split="train"))
    second = list(convert([record("hello", ["joy"], id="abc")], split="test"))
    third = list(convert([record("hello", ["joy"], id="abc")], split="train"))

    assert first[0]["id"] == "goemotions-train-abc"
    assert first[0]["id"] == third[0]["id"]
    assert second[0]["id"] != first[0]["id"]


def test_a_record_without_labels_is_an_error() -> None:
    with pytest.raises(ValueError, match="no labels field"):
        list(convert([{"text": "hello"}]))


def test_conversion_report_counts_labels_and_unmapped() -> None:
    rows = list(
        convert(
            [record("a", ["joy"]), record("b", ["joy", "sadness"]), record("c", ["surprise"])],
            keep_unmapped=True,
        )
    )

    report = conversion_report(rows)

    assert report["rows"] == 3
    assert report["by_label"] == {"joy": 1, "sadness": 1, "unmapped": 1}
    assert report["unmapped"] == 1
    assert report["languages"] == {"en": 3}
