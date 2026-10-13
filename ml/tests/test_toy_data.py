"""The toy fixture and its splits: deterministic bytes, honest contents.

These tests are also the fixture's editorial rules: no crisis language, no
personal names, all four language variants present, every label in every split.
If someone adds a sentence that breaks one of them, this file says so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manovia_ml.data.toy import (
    TOY_LABELS,
    build_splits,
    default_fixture_path,
    load_fixture,
    split_rows,
    verify_manifest,
)


def test_fixture_loads_and_covers_every_label_and_language() -> None:
    rows = load_fixture()

    assert len(rows) == 84
    assert {row["label"] for row in rows} == set(TOY_LABELS)
    assert {row["lang"] for row in rows} == {"en", "hi", "hi-Latn", "bn"}


def test_fixture_contains_no_crisis_language() -> None:
    """A fixture inside the safety path must not itself contain a crisis signal."""

    banned = (
        "suicide",
        "kill myself",
        "self-harm",
        "self harm",
        "end my life",
        "आत्महत्या",
        "আত্মহত্যা",
    )
    rows = load_fixture()

    for row in rows:
        lowered = str(row["text"]).lower()
        assert not any(term in lowered for term in banned), row["id"]


def test_fixture_is_hand_written_not_scraped() -> None:
    """No URLs, no handles, no personal names: invented situations only."""

    rows = load_fixture()

    for row in rows:
        text = str(row["text"])
        assert "http" not in text
        assert "@" not in text
        assert not any(char.isdigit() and char not in "१२३४५६७८९০১২৩৪৫৬৭৮৯" for char in text), row[
            "id"
        ]


def test_split_is_deterministic_and_stratified(tmp_path: Path) -> None:
    first = build_splits(out_dir=tmp_path / "a", seed=7, train_fraction=0.7, prefix="t")
    second = build_splits(out_dir=tmp_path / "b", seed=7, train_fraction=0.7, prefix="t")

    assert first.train.sha256 == second.train.sha256
    assert first.train.rows == 60
    assert first.eval.rows == 24
    # Six labels x 14 rows: the split keeps every label in both halves.
    assert first.train.labels and set(first.train.labels) == set(TOY_LABELS)
    assert set(first.eval.labels) == set(TOY_LABELS)


def test_binary_slice_is_a_subset_of_the_fixture(tmp_path: Path) -> None:
    manifest = build_splits(
        out_dir=tmp_path / "p",
        seed=20261013,
        train_fraction=0.7,
        labels=["joy", "anger"],
        prefix="s",
    )
    rows = load_fixture()
    joy_anger = {row["id"] for row in rows if row["label"] in {"joy", "anger"}}

    train = set(manifest.train.labels or {})
    assert train == {"joy", "anger"}
    assert manifest.train.rows == 20
    assert manifest.eval.rows == 8
    assert len(joy_anger) == 28 == manifest.train.rows + manifest.eval.rows


def test_a_different_seed_moves_rows_between_the_splits(tmp_path: Path) -> None:
    first = build_splits(out_dir=tmp_path / "a", seed=1, train_fraction=0.7, prefix="t")
    second = build_splits(out_dir=tmp_path / "b", seed=2, train_fraction=0.7, prefix="t")

    assert first.train.sha256 != second.train.sha256


def test_split_rows_rejects_unknown_and_single_labels() -> None:
    rows = load_fixture()

    with pytest.raises(ValueError, match="unknown labels"):
        split_rows(rows, seed=1, train_fraction=0.7, labels=["joy", "nope"])
    with pytest.raises(ValueError, match="at least two labels"):
        split_rows(rows, seed=1, train_fraction=0.7, labels=["joy"])


@pytest.mark.parametrize("fraction", [0.0, 1.0, -0.1, 1.5])
def test_split_rows_rejects_impossible_fractions(fraction: float) -> None:
    with pytest.raises(ValueError, match="train_fraction"):
        split_rows(load_fixture(), seed=1, train_fraction=fraction)


def test_load_fixture_rejects_a_missing_label(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text('{"id": "x", "text": "hello", "lang": "en"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="missing"):
        load_fixture(path)


def test_load_fixture_rejects_a_duplicate_id(tmp_path: Path) -> None:
    path = tmp_path / "dupe.jsonl"
    row = '{"id": "x", "text": "hello", "lang": "en", "label": "joy"}'
    path.write_text(f"{row}\n{row}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate id"):
        load_fixture(path)


def test_load_fixture_rejects_an_unknown_label(tmp_path: Path) -> None:
    path = tmp_path / "unknown.jsonl"
    path.write_text(
        '{"id": "x", "text": "hello", "lang": "en", "label": "gloom"}\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="unknown label"):
        load_fixture(path)


def test_load_fixture_rejects_an_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        load_fixture(path)


def test_manifest_round_trips_and_verifies(tmp_path: Path) -> None:
    manifest = build_splits(out_dir=tmp_path / "p", seed=5, train_fraction=0.7, prefix="t")

    problems = verify_manifest(
        tmp_path / "p" / "t.manifest.json",
        train_path=tmp_path / "p" / "t_train.jsonl",
        eval_path=tmp_path / "p" / "t_eval.jsonl",
    )

    assert problems == []
    assert manifest.as_dict()["generator_version"] == "1"
    assert manifest.as_dict()["splits"]["train"]["rows"] == 60


def test_verify_manifest_detects_a_tampered_split(tmp_path: Path) -> None:
    build_splits(out_dir=tmp_path / "p", seed=5, train_fraction=0.7, prefix="t")
    train = tmp_path / "p" / "t_train.jsonl"
    train.write_text(train.read_text(encoding="utf-8") + '{"id": "extra"}\n', encoding="utf-8")

    problems = verify_manifest(
        tmp_path / "p" / "t.manifest.json",
        train_path=train,
        eval_path=tmp_path / "p" / "t_eval.jsonl",
    )

    assert problems and "sha256 mismatch" in problems[0]


def test_manifest_paths_are_relative_to_the_ml_root(tmp_path: Path) -> None:
    """A manifest that embeds a machine's absolute paths is not shareable."""

    default_fixture = build_splits(out_dir=tmp_path / "p", seed=5, train_fraction=0.7, prefix="t")
    manifest_path = tmp_path / "p" / "t.manifest.json"

    assert default_fixture.source.path == str(
        default_fixture_path().resolve().relative_to(default_fixture_path().resolve().parents[2])
    )
    assert '"path": "data/' in manifest_path.read_text(encoding="utf-8")
