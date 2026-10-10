"""The Day 9 eval dataset's integrity contracts.

The dataset is synthetic data we ship, so its invariants are testable: size,
vocabulary, uniqueness, method-word screening, split disjointness, and — the
one that matters most — the frozen test split. The test file's SHA-256 is
recorded in ``evals/datasets/manifest.json`` at the moment thresholds were
chosen; any drift fails here. The training and tuning scripts must also never
even *name* the test file.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASET_DIR = REPO_ROOT / "evals" / "datasets"

sys.path.insert(0, str(REPO_ROOT / "evals"))

from build_crisis_dataset import (  # type: ignore[import-not-found]  # noqa: E402
    CATEGORIES,
    LANGUAGES,
    LEVELS,
    validate_cases,
)

SPLITS = ("train", "dev", "test")


def _load(name: str) -> list[dict[str, str]]:
    path = DATASET_DIR / f"crisis_cases_{name}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture(scope="module")
def manifest() -> dict[str, object]:
    data: dict[str, object] = json.loads(
        (DATASET_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    return data


def test_the_dataset_is_large_enough() -> None:
    cases = _load("train") + _load("dev") + _load("test")
    assert len(cases) >= 300, "the Day 9 contract is 300+ labelled cases"


def test_every_case_passes_the_builders_own_validation() -> None:
    cases = _load("train") + _load("dev") + _load("test")
    assert validate_cases(cases) == []


def test_labels_languages_and_categories_are_the_known_vocabularies() -> None:
    cases = _load("train") + _load("dev") + _load("test")
    for case in cases:
        assert case["label"] in LEVELS
        assert case["lang"] in LANGUAGES
        assert case["category"] in CATEGORIES
        assert case["notes"].strip()


def test_no_text_is_duplicated_across_the_dataset() -> None:
    cases = _load("train") + _load("dev") + _load("test")
    texts = [case["text"].casefold() for case in cases]
    assert len(texts) == len(set(texts))


def test_the_splits_are_disjoint_and_cover_the_whole_file() -> None:
    everything = _load("train") + _load("dev") + _load("test")
    whole = {
        case["id"]
        for case in [
            json.loads(line)
            for line in (DATASET_DIR / "crisis_cases.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
    }
    split_ids = [case["id"] for case in everything]
    assert len(split_ids) == len(set(split_ids)), "a case appears in two splits"
    assert set(split_ids) == whole


def test_every_label_and_language_appears_in_every_split() -> None:
    """A split missing a whole class would silently break training or eval."""
    for name in SPLITS:
        cases = _load(name)
        assert {case["label"] for case in cases} == set(LEVELS), name
        assert {case["lang"] for case in cases} == set(LANGUAGES), name


def test_hard_families_are_present() -> None:
    """The task's families: hard negatives and hard positives must exist."""
    cases = _load("train") + _load("dev") + _load("test")
    notes = [case["notes"].casefold() for case in cases]
    for family in ("hard negative", "hard positive", "recovery story", "figurative", "farewell"):
        assert any(family in note for note in notes), f"missing family: {family}"
    # Indirect crisis phrasing: at least some HIGH/IMMINENT cases whose text
    # contains none of the obvious English keywords.
    indirect = [
        case
        for case in cases
        if case["label"] in ("HIGH", "IMMINENT")
        and not re.search(r"suicid|kill|die|dying|dead|end my life", case["text"], re.IGNORECASE)
    ]
    assert len(indirect) >= 20


def test_the_test_split_is_frozen(manifest: dict[str, object]) -> None:
    """The hash in the manifest is the contract; drift fails the build."""
    splits = manifest["splits"]
    assert isinstance(splits, dict)
    for name in SPLITS:
        entry = splits[name]
        assert isinstance(entry, dict)
        content = (DATASET_DIR / f"crisis_cases_{name}.jsonl").read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], f"{name} drifted"
    test_entry = splits["test"]
    assert isinstance(test_entry, dict) and test_entry["frozen"] is True


def test_nothing_that_builds_or_tunes_the_model_mentions_the_test_split() -> None:
    """Training and threshold tuning must not even open the frozen file."""
    for script in ("train_safety_classifier.py", "tune_safety_thresholds.py"):
        source = (REPO_ROOT / "evals" / script).read_text(encoding="utf-8")
        assert "crisis_cases_test" not in source, f"{script} touches the frozen split"
        assert 'load_split("test")' not in source, f"{script} touches the frozen split"


def test_no_case_names_a_method() -> None:
    """The builder validates this at generation; re-assert it from the files.

    Synthetic crisis data that described methods would be unsafe training
    material, so this check fails the build even if the builder is bypassed.
    """
    cases = _load("train") + _load("dev") + _load("test")
    problems = [problem for problem in validate_cases(cases) if "method word" in problem]
    assert problems == []
