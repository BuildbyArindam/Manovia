"""The synthetic corpus: provenance, screens, and a split that cannot leak.

The properties that matter for a *safety* training set, and the reason each one
is tested:

* **Provenance on every row.** A row whose origin cannot be read is a row nobody
  can review, so ``provenance`` is part of the shape and the loader keeps it.
* **Nothing is dropped silently.** Every drop carries a reason, and the counts
  reconcile: seeds + paraphrases + adversarial = kept + dropped.
* **The method-word screen.** The Day-9 eval set forbids method words; a training
  set that contained them would teach the model the one shortcut that matters
  most.
* **The split never produces a test file.** The held-out split for this model is
  the frozen Day-9 file, and there is no code path here that writes one.
* **A stratum cannot be swallowed.** Two rows in one stratum do not both land in
  training, and a rare level cannot vanish from dev.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from pathlib import Path

import pytest

from manovia_ml.paths import ml_root
from manovia_ml.training.safety.augment import (
    ADVERSARIAL_TRANSFORMS,
    ExpansionConfig,
    ExpansionResult,
    GeneratedRow,
    RulesVerdict,
    adversarial_variants,
    code_mix,
    emoji,
    expand,
    method_word_problems,
    romanise,
    typo,
)
from manovia_ml.training.safety.data import (
    apportion,
    assign_splits,
    dataset_ref,
    load_examples,
    near_duplicate_report,
    rows_to_examples,
    summary,
    write_examples,
)
from manovia_ml.training.safety.providers import FakeParaphraser

SEEDS: list[dict[str, object]] = [
    {
        "id": "seed-1",
        "text": "i cannot keep doing this anymore",
        "lang": "en",
        "label": "high",
        "category": "suicidal_ideation",
        "notes": "indirect phrasing",
    },
    {
        "id": "seed-2",
        "text": "i have a plan for tonight",
        "lang": "en",
        "label": "imminent",
        "category": "intent_plan",
        "notes": "explicit plan",
    },
    {
        "id": "seed-3",
        "text": "work was hard but i am okay",
        "lang": "en",
        "label": "none",
        "category": "none",
        "notes": "hard negative",
    },
    {
        "id": "seed-4",
        "text": "मैं रोज़ यही सोचता हूँ",
        "lang": "hi",
        "label": "medium",
        "category": "severe_hopelessness",
        "notes": "hindi",
    },
    {
        "id": "seed-5",
        "text": "আমি আর সামলতে পারছি না",
        "lang": "bn",
        "label": "low",
        "category": "none",
        "notes": "bengali",
    },
]


def _expand(
    *,
    paraphrases: int = 3,
    adversarial: int = 1,
    seed: int = 20261013,
    assessor: Callable[[str, str], RulesVerdict] | None = None,
) -> ExpansionResult:
    return expand(
        SEEDS,
        paraphraser=FakeParaphraser(),
        config=ExpansionConfig(
            paraphrases_per_seed=paraphrases,
            adversarial_per_row=adversarial,
            seed=20261013,
        ),
        rules_assessor=assessor,
        generator_name="fake-v1:fake-v1",
    )


# --------------------------------------------------------------------------- #
# Provenance                                                                   #
# --------------------------------------------------------------------------- #


def test_every_generated_row_carries_its_provenance() -> None:
    result = _expand()

    assert result.rows, "the expansion produced nothing"
    for row in result.rows:
        payload = row.as_dict()
        provenance = payload["provenance"]
        assert provenance["source"] in {"seed", "paraphrase", "adversarial"}
        assert provenance["seed_id"]
        assert provenance["generator"]
        assert provenance["strategy"]
        assert provenance["rule_id"]
        assert isinstance(provenance["transforms"], list)


def test_the_seed_rows_are_kept_verbatim() -> None:
    result = _expand()

    seeds = {row.text for row in result.rows if row.source == "seed"}
    assert seeds == {str(seed["text"]) for seed in SEEDS}
    for row in result.rows:
        if row.source == "seed":
            assert row.transforms == ()


def test_a_paraphrase_keeps_the_label_and_the_seed_id() -> None:
    result = _expand()

    paraphrases = [row for row in result.rows if row.source == "paraphrase"]
    assert paraphrases, "the Fake produced no paraphrases"
    for row in paraphrases:
        seed = next(item for item in SEEDS if str(item["id"]) == row.seed_id)
        assert row.label == seed["label"]
        assert row.lang == seed["lang"]
        assert row.category == seed["category"]


def test_the_expansion_is_deterministic() -> None:
    first = _expand()
    second = _expand()

    assert [row.text for row in first.rows] == [row.text for row in second.rows]


def test_counts_reconcile() -> None:
    """Nothing disappears: what went in is either kept or dropped with a reason."""

    result = _expand()

    assert result.stats["seeds"] == len(SEEDS)
    assert result.stats["kept"] == len(result.rows)
    assert result.stats["dropped"] == len(result.dropped)
    assert sum(result.drop_reasons.values()) == len(result.dropped)
    for row_id, reason in result.dropped:
        assert row_id and reason


# --------------------------------------------------------------------------- #
# The screens                                                                  #
# --------------------------------------------------------------------------- #


def test_the_method_word_screen_catches_the_words_that_matter() -> None:
    """A training row naming a method teaches the model to look for the method."""

    assert method_word_problems("i want to hang myself tonight")
    assert method_word_problems("मैं ज़हर ले लूँगा")
    assert not method_word_problems("i cannot keep doing this anymore")
    assert not method_word_problems("")


def test_an_adversarial_variant_still_says_something() -> None:
    """Each transform names one thing that changed, and the row stays non-empty."""

    rng = random.Random(7)
    row = GeneratedRow(
        id="seed-1",
        text="i cannot keep doing this anymore",
        lang="en",
        label="high",
        category="suicidal_ideation",
        notes="indirect phrasing",
        source="seed",
        seed_id="seed-1",
        generator="test",
        strategy="seed",
        rule_id="seed",
    )

    variants = adversarial_variants(row, count=len(ADVERSARIAL_TRANSFORMS), rng=rng)

    assert variants
    for variant in variants:
        assert variant.text.strip()
        assert len(variant.transforms) == 1
        assert variant.source == "adversarial"
        assert variant.seed_id == "seed-1"


def test_the_transforms_do_what_they_say() -> None:
    rng = random.Random(11)

    assert typo("i cannot keep doing this", rng) != "i cannot keep doing this"
    assert romanise("मैं ठीक हूँ", "hi") != "मैं ठीक हूँ"
    # Code-mixing splices English into an Indic sentence, so an English sentence
    # has nothing to splice into and is returned unchanged — that is the contract.
    assert code_mix("i feel awful", "en", rng) == "i feel awful"
    assert code_mix("मैं ठीक हूँ", "hi", rng) != "मैं ठीक हूँ"
    assert emoji("i feel awful", "high", rng) != "i feel awful"


def test_the_rules_filter_drops_a_row_the_engine_disagrees_with() -> None:
    """A *stricter* engine is a contradiction; a *more lenient* one is the point.

    The model exists to catch what the rules miss, so a row labelled HIGH that the
    rules call NONE is kept. The reverse — a benign row the rules escalate — is a
    contradiction this pipeline cannot resolve, so it is dropped and counted.
    """

    def assessor(text: str, lang: str) -> RulesVerdict:
        return RulesVerdict(level="imminent")

    result = _expand(paraphrases=0, adversarial=0, assessor=assessor)

    assert result.dropped, "the filter kept rows the rules engine escalated"
    assert set(result.drop_reasons) == {"rules_inconsistent"}
    # Only the benign rows survive: the crisis rows agree with "imminent".
    assert all(row.label == "imminent" for row in result.rows)


def test_a_more_lenient_engine_keeps_every_crisis_row() -> None:
    """The gap the model is for must not be filtered away."""

    def assessor(text: str, lang: str) -> RulesVerdict:
        return RulesVerdict(level="none")

    result = _expand(paraphrases=0, adversarial=0, assessor=assessor)

    assert not result.dropped
    assert len(result.rows) == len(SEEDS)


def test_without_an_assessor_the_reason_is_recorded_not_invented() -> None:
    """No backend import is not a failure; it is a stated limitation."""

    result = _expand(paraphrases=1, adversarial=0)

    assert result.rows
    assert any(row.rules_agrees for row in result.rows)
    # The assessor was absent, so nothing was dropped for label disagreement.
    assert "rules_inconsistent" not in result.drop_reasons


# --------------------------------------------------------------------------- #
# The split                                                                    #
# --------------------------------------------------------------------------- #


def test_a_two_row_stratum_is_never_entirely_in_training() -> None:
    """The bug this pins: naive rounding left tiny strata with nothing but train."""

    assert apportion(2, [0.8, 0.1, 0.1]) == [1, 1, 0]
    assert apportion(3, [0.8, 0.1, 0.1]) == [1, 1, 1]


def test_apportion_always_sums_to_n_and_gives_everyone_one() -> None:
    for n in range(1, 40):
        counts = apportion(n, [0.8, 0.1, 0.1])
        assert sum(counts) == n, n
        if n >= 3:
            assert min(counts) >= 1, (n, counts)
        if n < 3:
            assert sum(1 for count in counts if count) == n, (n, counts)


def test_apportion_rejects_degenerate_shares() -> None:
    with pytest.raises(ValueError):
        apportion(10, [0.0, 0.0])


# Both splitter tests below read the *committed* fixture, not the generated
# `data/safety_toy/train.jsonl`. That generated file is a DVC output (gitignored,
# written by `dvc repro`) and, on a fresh clone where `dvc repro` has not run,
# these two tests died with FileNotFoundError — a red `make ml-test` out of the
# box. Reading it was also the weaker test: the builder had already split those
# rows, so "re-splitting a train-only file gives no test slice" was partly
# circular. The committed 80-row fixture covers all five risk levels and all four
# languages, so the same property is now proven on the input the builder starts
# from. Regression: Day 49, fresh clone, `make ml-test` (see PROGRESS.md).


def test_the_split_has_no_test_split_by_default() -> None:
    """The held-out split is the frozen Day-9 file, not a splitter's output."""

    examples = load_examples(Path("data/toy/safety_toy.jsonl"))
    assert len(examples) >= 80

    splits = assign_splits(examples, seed=20261013)

    assert set(splits) == {"train", "dev", "calibration"}
    assert "test" not in splits
    assert sum(len(rows) for rows in splits.values()) == len(examples)


def test_the_toy_fixture_can_ask_for_a_fourth_slice() -> None:
    """The one exception, used only by the smoke config."""

    examples = load_examples(Path("data/toy/safety_toy.jsonl"))

    splits = assign_splits(examples, seed=20261013, train_share=0.7, dev_share=0.15, test_share=0.1)

    assert set(splits) == {"train", "dev", "calibration", "test"}
    assert splits["test"], "the explicit fourth slice must contain rows"
    total = sum(len(rows) for rows in splits.values())
    assert total == len(examples)


def test_the_split_is_stratified_and_disjoint() -> None:
    examples = load_examples(Path("data/toy/safety_toy.jsonl"))

    splits = assign_splits(
        examples, seed=20261013, train_share=0.65, dev_share=0.15, test_share=0.15
    )

    ids = [example.id for rows in splits.values() for example in rows]
    assert len(ids) == len(set(ids)) == len(examples)
    for name, rows in splits.items():
        levels = {row.label for row in rows}
        assert levels, f"{name} lost every level"
        assert len(rows) >= 5, name


def test_the_split_is_reproducible() -> None:
    examples = load_examples(Path("data/toy/safety_toy.jsonl"))

    shares = {"train_share": 0.7, "dev_share": 0.15, "test_share": 0.1}
    first = assign_splits(examples, seed=20261013, **shares)
    second = assign_splits(examples, seed=20261013, **shares)

    assert [row.id for row in first["train"]] == [row.id for row in second["train"]]
    assert [row.id for row in first["test"]] != [row.id for row in first["train"]]


def test_a_different_seed_moves_rows() -> None:
    examples = load_examples(Path("data/toy/safety_toy.jsonl"))

    shares = {"train_share": 0.7, "dev_share": 0.15, "test_share": 0.1}
    first = assign_splits(examples, seed=20261013, **shares)
    second = assign_splits(examples, seed=99, **shares)

    assert [row.id for row in first["train"]] != [row.id for row in second["train"]]


# --------------------------------------------------------------------------- #
# Leakage and round-tripping                                                   #
# --------------------------------------------------------------------------- #


def test_the_frozen_split_does_not_overlap_the_toy_fixture() -> None:
    """The smoke corpus must not contain the frozen rows (or near copies).

    Read-only: this test opens the frozen test split and nothing writes to it.
    The path is resolved from the ML root, because pytest runs from ``ml/`` and
    the frozen splits live deliberately outside it.
    """

    toy = load_examples(Path("data/toy/safety_toy.jsonl"))
    frozen = load_examples(ml_root().parent / "evals/datasets/crisis_cases_test.jsonl")

    report = near_duplicate_report(toy, frozen)

    assert report["exact_overlap"] == 0
    assert report["max_jaccard"] < 0.8
    assert report["near_duplicate_rows"] == 0


def test_rows_round_trip_through_jsonl_with_their_provenance(tmp_path: Path) -> None:
    result = _expand(paraphrases=2, adversarial=1)
    path = write_examples(tmp_path / "rows.jsonl", rows_to_examples(result.rows))

    reloaded = load_examples(path)

    assert len(reloaded) == len(result.rows)
    for original, restored in zip(result.rows, reloaded, strict=True):
        assert restored.text == original.text
        assert restored.label == original.label
        assert restored.lang == original.lang
        assert restored.provenance["seed_id"] == original.seed_id


def test_the_summary_counts_what_the_report_quotes() -> None:
    result = _expand(paraphrases=2, adversarial=1)

    stats = summary(rows_to_examples(result.rows))

    assert stats["rows"] == len(result.rows)
    assert stats["labels"]["high"] == sum(1 for row in result.rows if row.label == "high")
    assert stats["languages"]["en"] == sum(1 for row in result.rows if row.lang == "en")
    assert stats["provenance_sources"]["adversarial"] == sum(
        1 for row in result.rows if row.source == "adversarial"
    )


def test_a_dataset_ref_records_the_hash_and_the_rows(tmp_path: Path) -> None:
    path = write_examples(tmp_path / "rows.jsonl", rows_to_examples(_expand(paraphrases=1).rows))

    ref = dataset_ref("train", path)

    assert ref.name == "train"
    assert ref.rows == len(load_examples(path))
    assert len(ref.sha256) == 64


def test_the_jsonl_is_valid_json_per_line(tmp_path: Path) -> None:
    path = write_examples(tmp_path / "rows.jsonl", rows_to_examples(_expand(paraphrases=1).rows))

    for line in path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        assert set(payload) >= {"id", "text", "lang", "label", "category", "provenance"}


def test_the_built_manifest_is_byte_stable(tmp_path: Path) -> None:
    """Two builds of the same fixture must agree byte for byte.

    Regression, Day 49: the manifest carried ``"built": <today>`` and an
    absolute ``seed_files[].path``. Both are DVC outputs, so ``dvc.lock``
    changed on every calendar day and on every checkout directory —
    ``dvc status`` was never clean, and a lock that moves without the data
    moving cannot be a reproducibility gate. Checked here rather than only in
    CI because the bug is invisible on the day the lock was written.
    """

    from manovia_ml.training.safety.build_data import _build_toy

    out = tmp_path / "build"
    _build_toy(out_dir=out, seed=20261013)
    first = (out / "manifest.json").read_bytes()
    _build_toy(out_dir=out, seed=20261013)
    second = (out / "manifest.json").read_bytes()

    assert first == second, "the same build twice must produce the same bytes"

    manifest = json.loads(first)
    assert "built" not in manifest, "a wall-clock date in a hashed artefact breaks the lock"
    assert not Path(manifest["seed_files"][0]["path"]).is_absolute()
    assert manifest["seed_files"][0]["path"].endswith("data/toy/safety_toy.jsonl")

    # Split refs are relative too, so the lock does not depend on the checkout
    # directory. Checked on a committed fixture under ml/ (the pipeline's own
    # `data/safety_toy` out_dir is the same shape).
    ref = dataset_ref("toy", ml_root() / "data/toy/safety_toy.jsonl")
    assert ref.path == "data/toy/safety_toy.jsonl", ref.path
