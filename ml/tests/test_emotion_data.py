"""Data loading, conversion and the train/held-out overlap check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manovia_ml.training.emotion.data import (
    DEFAULT_VALIDATION_PATH,
    EmotionExample,
    convert_source_rows,
    default_validation_path,
    example_dataset_ref,
    load_examples,
    load_validation,
    normalise_text,
    overlap_report,
    summary,
    to_binary_targets,
    to_soft_targets,
    write_examples,
)


def _write(path: Path, rows: list[dict[str, object]]) -> Path:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def test_load_single_label_and_multi_label_rows(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "split.jsonl",
        [
            {"id": "a", "text": "one", "label": "joy", "lang": "en"},
            {"id": "b", "text": "two", "labels": ["fear", "anxiety"], "lang": "hi"},
        ],
    )
    examples = load_examples(path)
    assert [example.labels for example in examples] == [("joy",), ("fear", "anxiety")]
    # Taxonomy order, regardless of the order in the file.
    assert examples[1].labels == ("fear", "anxiety")


def test_unknown_label_is_an_error_not_a_zero_column(tmp_path: Path) -> None:
    path = _write(tmp_path / "split.jsonl", [{"id": "a", "text": "one", "labels": ["excited"]}])
    with pytest.raises(ValueError, match="not in Manovia's taxonomy"):
        load_examples(path)


def test_empty_text_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path / "split.jsonl", [{"id": "a", "text": "   ", "labels": ["joy"]}])
    with pytest.raises(ValueError, match="empty"):
        load_examples(path)


def test_round_trip_write_and_read(tmp_path: Path) -> None:
    examples = [
        EmotionExample(id="a", text="text", labels=("joy",), lang="en", source="test"),
        EmotionExample(
            id="b",
            text="more",
            labels=("anxiety",),
            lang="bn",
            source="test",
            weights=(("anxiety", 0.9),),
        ),
    ]
    path = write_examples(tmp_path / "out.jsonl", examples)
    assert load_examples(path) == examples


def test_targets_use_the_soft_weights_when_present() -> None:
    examples = [
        EmotionExample(
            id="a", text="t", labels=("anger",), lang="en", source="s", weights=(("anger", 0.6),)
        ),
        EmotionExample(id="b", text="u", labels=("joy",), lang="en", source="s"),
    ]
    soft = to_soft_targets(examples, ["joy", "anger"])
    assert soft == [[0.0, 0.6], [1.0, 0.0]]
    assert to_binary_targets(examples, ["joy", "anger"]) == [[0, 1], [1, 0]]


def test_summary_counts_labels_languages_and_multi_label_rows() -> None:
    examples = [
        EmotionExample(id="a", text="t", labels=("joy",), lang="en", source="s"),
        EmotionExample(id="b", text="u", labels=("joy", "calm"), lang="hi", source="s"),
    ]
    counted = summary(examples)
    assert counted["rows"] == 2
    assert counted["languages"] == {"en": 1, "hi": 1}
    assert counted["labels"]["joy"] == 2
    assert counted["multi_label_rows"] == 1
    assert "loneliness" in counted["labels_absent"]


def test_normalise_text_ignores_case_punctuation_and_unicode_form() -> None:
    assert normalise_text("I can't  sleep!!") == normalise_text("i can t sleep")
    assert normalise_text("बारिश") == normalise_text("बारिश")


def test_overlap_finds_exact_duplicates() -> None:
    train = [
        EmotionExample(
            id="t1",
            text="the picnic was cancelled today",
            labels=("sadness",),
            lang="en",
            source="s",
        )
    ]
    held_out = [
        EmotionExample(
            id="h1",
            text="The picnic was cancelled, today!",
            labels=("sadness",),
            lang="en",
            source="s",
        )
    ]
    report = overlap_report(train, held_out)
    assert not report.clean
    assert report.exact == (("t1", "h1"),)
    assert "exact duplicate" in report.fail_message()


def test_overlap_finds_near_duplicates_that_are_not_identical() -> None:
    train = [
        EmotionExample(
            id="t1",
            text="my flight got cancelled and I spent my birthday in an airport",
            labels=("sadness",),
            lang="en",
            source="s",
        )
    ]
    held_out = [
        EmotionExample(
            id="h1",
            text="my flight was cancelled so I spent my birthday in the airport",
            labels=("sadness",),
            lang="en",
            source="s",
        )
    ]
    report = overlap_report(train, held_out)
    assert report.near, "a paraphrase of a training row must be caught"
    assert report.near[0][0] == "t1"
    assert report.near[0][1] == "h1"


def test_overlap_is_clean_for_unrelated_rows_and_scales() -> None:
    train = [
        EmotionExample(
            id=f"t{index}",
            text=f"training sentence number {index} about ordinary things happening",
            labels=("joy",),
            lang="en",
            source="s",
        )
        for index in range(2000)
    ]
    held_out = [
        EmotionExample(
            id="h1",
            text="a completely different sentence about the weather in another city",
            labels=("calm",),
            lang="en",
            source="s",
        ),
        EmotionExample(
            id="h2",
            text="training sentence number 1999 about ordinary things happening",
            labels=("joy",),
            lang="en",
            source="s",
        ),
    ]
    report = overlap_report(train, held_out)
    assert report.exact == (("t1999", "h2"),)
    assert report.near == ()
    assert not report.clean


def test_example_dataset_ref_counts_every_label_of_a_multi_label_row(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "split.jsonl",
        [
            {"id": "a", "text": "one", "labels": ["fear", "anxiety"], "lang": "hi"},
            {"id": "b", "text": "two", "labels": ["joy"], "lang": "en"},
        ],
    )
    ref = example_dataset_ref(path, load_examples(path), name="demo")
    assert ref.rows == 2
    assert ref.labels == {"fear": 1, "anxiety": 1, "joy": 1}
    assert ref.languages == {"hi": 1, "en": 1}
    assert len(ref.sha256) == 64


def test_convert_goemotions_rows_reports_what_it_dropped() -> None:
    rows = [
        {"id": "1", "text": "so happy today", "labels": ["joy"]},
        {"id": "2", "text": "I am so nervous", "labels": ["nervousness"]},
        {"id": "3", "text": "nothing much", "labels": ["neutral"]},
        {"id": "4", "text": "what a surprise", "labels": ["surprise"]},
        {"id": "5", "text": "bit annoyed", "labels": ["annoyance"]},
    ]
    examples, report = convert_source_rows(rows, scheme="goemotions", lang="en")
    by_id = {example.id: example for example in examples}
    assert by_id["1"].labels == ("joy",)
    assert by_id["2"].labels == ("anxiety",)
    assert by_id["3"].labels == ("neutral",)
    assert by_id["5"].labels == ("anger",)
    assert by_id["5"].weights == (("anger", 0.6),)
    assert "4" not in by_id  # {surprise} is not "no emotion": the row is dropped
    assert report["rows_in"] == 5
    assert report["rows_out"] == 4
    assert report["rows_neutral_only"] == 1
    assert report["dropped"]["label:surprise"] == 1
    assert report["labels_unreachable_from_this_scheme"] == ["loneliness"]


def test_convert_emonoba_column_style_rows() -> None:
    rows = [
        {
            "text": "one",
            "labels": {"anger": 1, "fear": 0, "joy": 0, "love": 0, "sadness": 0, "surprise": 0},
        },
        {
            "text": "two",
            "labels": {"anger": 0, "fear": 0, "joy": 0, "love": 1, "sadness": 0, "surprise": 0},
        },
    ]
    examples, report = convert_source_rows(rows, scheme="emonoba", lang="bn")
    assert [example.labels for example in examples] == [("anger",)]
    assert report["dropped"]["label:love"] == 1
    assert report["dropped"]["no_target"] == 1


def test_the_committed_held_out_set_is_what_the_card_claims() -> None:
    """The dataset card makes countable claims; this is where they are checked."""

    path = default_validation_path()
    assert path == (Path(__file__).resolve().parents[1] / DEFAULT_VALIDATION_PATH)
    examples = load_validation(path)
    assert len(examples) >= 150, "the Day-32 brief asks for at least 150 held-out rows"
    counted = summary(examples)
    assert set(counted["languages"]) == {"en", "hi", "bn"}
    assert counted["multi_label_rows"] >= 6, "multi-label behaviour must be exercised"
    assert counted["labels_absent"] == [], "every emotion needs rows, or per-label F1 is empty"
    assert all(example.source == "handwritten" for example in examples)
    assert len({example.id for example in examples}) == len(examples)
    assert len({normalise_text(example.text) for example in examples}) == len(examples)


def test_the_held_out_set_does_not_overlap_the_toy_training_fixture() -> None:
    """The committed check the verification run also performs by hand."""

    from manovia_ml.data.toy import load_fixture

    fixture = load_fixture()
    train = [
        EmotionExample(
            id=str(row.get("id")),
            text=str(row["text"]),
            labels=(str(row["label"]),),
            lang=str(row.get("lang", "en")),
            source="toy",
        )
        for row in fixture
    ]
    report = overlap_report(train, load_validation())
    assert report.clean, report.fail_message()
