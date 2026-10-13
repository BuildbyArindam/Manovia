"""Hashing and JSONL reading: a run must be able to say which bytes it read."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manovia_ml.hashing import (
    DatasetRef,
    check_expected,
    dataset_ref,
    read_jsonl,
    sha256_bytes,
    sha256_file,
    sha256_text,
)


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_sha256_matches_the_reference_value() -> None:
    # The canonical empty-string hash: if this breaks, the algorithm changed.
    assert sha256_bytes(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_text("abc") == sha256_bytes(b"abc")


def test_sha256_file_is_byte_exact(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_bytes(b"hello\n")

    assert sha256_file(path) == sha256_bytes(b"hello\n")


def test_read_jsonl_skips_blank_lines_and_keeps_order(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text('{"id": 1}\n\n{"id": 2}\n', encoding="utf-8")

    assert read_jsonl(path) == [{"id": 1}, {"id": 2}]


def test_read_jsonl_names_the_offending_line(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text('{"id": 1}\nnot json\n', encoding="utf-8")

    with pytest.raises(ValueError, match=r":2: invalid JSON"):
        read_jsonl(path)


def test_read_jsonl_rejects_non_object_rows(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text("[1, 2]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected a JSON object"):
        read_jsonl(path)


def test_dataset_ref_counts_labels_and_languages(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "split.jsonl",
        [
            {"id": "1", "text": "a", "label": "joy", "lang": "en"},
            {"id": "2", "text": "b", "label": "joy", "lang": "hi"},
            {"id": "3", "text": "c", "label": "anger", "lang": "en"},
        ],
    )

    ref = dataset_ref(path, name="train", rows=read_jsonl(path))

    assert ref.rows == 3
    assert ref.labels == {"joy": 2, "anger": 1}
    assert ref.languages == {"en": 2, "hi": 1}
    assert ref.sha256 == sha256_file(path)
    assert set(ref.as_params("data")) == {
        "data.train.path",
        "data.train.sha256",
        "data.train.rows",
        "data.train.lang.en",
        "data.train.lang.hi",
        "data.train.label.joy",
        "data.train.label.anger",
    }
    assert ref.as_dict()["labels"] == {"joy": 2, "anger": 1}


def test_dataset_ref_hashes_bytes_not_parsed_rows(tmp_path: Path) -> None:
    """A parser change must not look like a data change (and vice versa)."""

    path = write_jsonl(tmp_path / "split.jsonl", [{"id": "1", "text": "a", "label": "joy"}])
    ref = dataset_ref(path, name="train")
    # Same rows (same key order in the parsed dict), different bytes.
    path.write_text('{"label":"joy","text":"a","id":"1"}\n', encoding="utf-8")
    reformatted = dataset_ref(path, name="train")

    assert ref.as_dict()["labels"] == reformatted.as_dict()["labels"]
    assert ref.rows == reformatted.rows
    assert ref.sha256 != reformatted.sha256


def test_check_expected_passes_on_the_pinned_version(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "split.jsonl", [{"text": "a", "label": "joy"}])
    ref = dataset_ref(path, name="train")

    check_expected(ref, sha256=ref.sha256, rows=1)


def test_check_expected_fails_closed_on_a_hash_mismatch() -> None:
    ref = DatasetRef(name="train", path="data/x.jsonl", sha256="b" * 64, rows=1)

    with pytest.raises(ValueError, match="sha256 mismatch") as excinfo:
        check_expected(ref, sha256="a" * 64, rows=None)

    assert "a" * 64 in str(excinfo.value)  # names both sides, not just "wrong"


def test_check_expected_fails_closed_on_a_row_count_mismatch() -> None:
    ref = DatasetRef(name="train", path="data/x.jsonl", sha256="a" * 64, rows=9)

    with pytest.raises(ValueError, match="expected 10 rows, found 9"):
        check_expected(ref, sha256=None, rows=10)
