"""The retrieval eval set: labels, splits, and the expansion that fills it out.

What is being protected here, and why each property has a test:

* **A label is a document id that exists.** A query pointing at a document that
  is not in the corpus scores as a permanent miss and reads as a retrieval bug
  forever after, so the loader refuses it.
* **Every document is labelled somewhere.** A document no query ever names can
  never be recalled, and a recall number computed over such a corpus flatters
  whatever happens to be labelled.
* **The split is a hash, not a shuffle.** ``sha256(seed:id) % 100`` means the
  test split is the same set of rows on every machine and in every run, and that
  adding a row cannot move an existing one — which is what makes "the test split
  was never tuned on" a checkable claim rather than a promise.
* **Hand-written rows can land in test; synthetic ones cannot.** A paraphrase
  generated from a hand-written row carries that row's vocabulary, so letting
  generated rows into the held-out split would measure the generator.
* **A synthetic row says what made it.** ``source``, ``generator``, ``seed_id``
  and ``spot_checked`` travel on the row, because a reviewer has to be able to
  find the human-written original of anything they are asked to trust.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from manovia_ml.retrieval.augment import GENERATOR_NAME, augment_row, expand
from manovia_ml.retrieval.corpus import (
    build_passages,
    corpus_fingerprint,
    load_documents,
)
from manovia_ml.retrieval.data import (
    SPLITS,
    EvalSetError,
    RetrievalQuery,
    assign_split,
    load_eval_set,
    load_split,
    split_fingerprint,
    split_rows,
    validate_against_corpus,
    write_splits,
)

SEED = 20261013


def query(row_id: str, text: str, docs: list[str], **extra: object) -> RetrievalQuery:
    payload: dict[str, object] = {
        "id": row_id,
        "query": text,
        "lang": "en",
        "style": "direct",
        "relevant_docs": docs,
    }
    payload.update(extra)
    return RetrievalQuery.model_validate(payload)


# --------------------------------------------------------------------- corpus #


class TestCorpus:
    def test_the_corpus_loads_and_every_document_has_a_language(self) -> None:
        docs = load_documents()
        assert len(docs) >= 40
        assert {doc.language for doc in docs} == {"en", "hi", "bn"}
        assert all(doc.title and doc.body for doc in docs)

    def test_passages_carry_the_title_anchored_text_an_embedder_sees(self) -> None:
        passages = build_passages(load_documents())
        assert passages
        for passage in passages:
            # The ingest embeds "<title>\n<title>\n\n<chunk>"; the training
            # pairs have to be built on the same string or the encoder is
            # trained on a distribution production never shows it.
            assert passage.embed_text.startswith(f"{passage.title}\n{passage.title}\n\n")
            assert passage.embed_text.endswith(passage.text)
            assert passage.chunk_id == f"{passage.doc_id}#{passage.chunk_index:02d}"

    def test_the_fingerprint_moves_when_the_corpus_moves(self) -> None:
        passages = build_passages(load_documents())
        assert corpus_fingerprint(passages) != corpus_fingerprint(passages[:-1])
        assert corpus_fingerprint(passages) == corpus_fingerprint(list(passages))


# ------------------------------------------------------------------ eval set #


class TestEvalSet:
    def test_the_committed_set_loads_and_is_large_enough_to_report_on(self) -> None:
        rows = load_eval_set(corpus_ids=[doc.id for doc in load_documents()])
        assert len(rows) >= 150
        assert len({row.id for row in rows}) == len(rows)

    def test_every_document_is_labelled_by_at_least_one_query(self) -> None:
        rows = load_eval_set()
        docs = {doc.id for doc in load_documents()}
        labelled = {doc for row in rows for doc in row.relevant_docs}
        assert docs - labelled == set()

    def test_the_languages_and_styles_the_brief_asks_for_are_present(self) -> None:
        rows = load_eval_set()
        languages: Counter[str] = Counter(row.lang for row in rows)
        styles: Counter[str] = Counter(row.style for row in rows)
        for expected in ("en", "hi", "bn", "hi-Latn", "bn-Latn"):
            assert languages[expected] > 0, expected
        for expected in ("vague", "hinglish", "no-answer", "keyword", "scenario"):
            assert styles[expected] > 0, expected

    def test_no_answer_rows_say_why_and_carry_no_labels(self) -> None:
        rows = load_eval_set()
        refused = [row for row in rows if row.style == "no-answer"]
        assert len(refused) >= 20
        for row in refused:
            assert row.relevant_docs == []
            assert row.has_answer is False
            assert row.no_answer_reason

    def test_a_label_pointing_outside_the_corpus_is_refused(self) -> None:
        with pytest.raises(EvalSetError, match="not in the corpus"):
            load_eval_set(corpus_ids=["sleep-basics-rhythm"])

    def test_validation_reports_the_shape_of_the_set(self) -> None:
        report = validate_against_corpus(load_eval_set())
        assert report["corpus_docs"] >= 40
        assert report["labelled_docs"] == report["corpus_docs"]
        assert report["never_labelled"] == []

    def test_a_label_that_no_longer_exists_is_refused(self) -> None:
        with pytest.raises(EvalSetError, match="missing from the corpus"):
            validate_against_corpus([query("rt-ghost", "text", ["document-that-was-deleted"])])

    def test_an_answerable_row_cannot_claim_it_has_no_answer(self) -> None:
        # `has_answer` is derived from the labels, so a row that disagrees with
        # itself is a row somebody edited by hand and did not finish.
        with pytest.raises(ValidationError, match="has_answer"):
            query("rt-bad", "text", ["sleep-basics-rhythm"], has_answer=False)

    def test_a_no_answer_row_cannot_carry_labels(self) -> None:
        with pytest.raises(ValidationError, match="has_answer"):
            query("rt-bad", "text", ["sleep-basics-rhythm"], style="no-answer")

    def test_a_no_answer_row_needs_a_reason(self) -> None:
        with pytest.raises(ValidationError, match="no_answer_reason"):
            query("rt-bad", "text", [], style="no-answer", has_answer=False)

    def test_a_duplicate_label_is_refused_not_collapsed(self) -> None:
        # Collapsing it would hide a copy-paste mistake in the row that a
        # reviewer is being asked to trust.
        with pytest.raises(ValidationError, match="duplicate id"):
            query("rt-dup", "text", ["a", "a", "b"])

    def test_a_romanised_row_is_labelled_as_one(self) -> None:
        # A code-mixed row is in the set *because* it is code-mixed; calling it
        # "direct" would make it an English row with a Hindi id, and the
        # per-language slices would report on the wrong thing.
        with pytest.raises(ValidationError, match="hi-Latn"):
            query(
                "rt-bad",
                "ghar pe akela feel hota hai",
                ["loneliness-small-contacts"],
                lang="hi-Latn",
                style="direct",
            )


# -------------------------------------------------------------------- splits #


class TestSplits:
    def test_the_assignment_is_a_pure_function_of_the_seed_and_the_id(self) -> None:
        row = query("rt-stable", "cannot sleep", ["sleep-basics-rhythm"])
        assert assign_split(row, seed=SEED) == assign_split(row, seed=SEED)
        assert assign_split(row, seed=SEED) in SPLITS

    def test_a_different_seed_gives_a_different_split_for_some_row(self) -> None:
        rows = load_eval_set()
        first = {row.id: assign_split(row, seed=SEED) for row in rows}
        second = {row.id: assign_split(row, seed=SEED + 1) for row in rows}
        assert first != second

    def test_splitting_is_deterministic_over_the_whole_set(self) -> None:
        rows = load_eval_set()
        grouped = split_rows(rows, seed=SEED)
        again = split_rows(rows, seed=SEED)
        assert {name: [row.id for row in grouped[name]] for name in SPLITS} == {
            name: [row.id for row in again[name]] for name in SPLITS
        }

    def test_every_row_lands_in_exactly_one_split(self) -> None:
        rows = load_eval_set()
        grouped = split_rows(rows, seed=SEED)
        total = sum(len(grouped[name]) for name in SPLITS)
        assert total == len(rows)
        ids = [row.id for name in SPLITS for row in grouped[name]]
        assert len(ids) == len(set(ids))

    def test_the_test_split_is_hand_written_only(self) -> None:
        # The property the whole "was the test split tuned on?" question rests
        # on: generated paraphrases can land in train and dev, never in test or
        # calibration, so the held-out numbers describe rows a person wrote.
        rows = load_eval_set()
        grouped = split_rows(rows, seed=SEED)
        assert all(row.source == "handwritten" for row in grouped["test"])
        assert all(row.source == "handwritten" for row in grouped["calibration"])

    def test_the_fingerprint_is_the_rows_not_their_order(self) -> None:
        rows = load_eval_set()
        grouped = split_rows(rows, seed=SEED)
        shuffled = list(reversed(grouped["test"]))
        assert split_fingerprint(grouped["test"]) == split_fingerprint(shuffled)
        assert split_fingerprint(grouped["test"]) != split_fingerprint(grouped["dev"])

    def test_writing_and_reading_a_split_round_trips(self, tmp_path: Path) -> None:
        rows = load_eval_set()
        source = tmp_path / "source.jsonl"
        source.write_text(
            "\n".join(json.dumps(row.model_dump(mode="json"), ensure_ascii=False) for row in rows),
            encoding="utf-8",
        )
        manifest = write_splits(rows, out_dir=tmp_path / "splits", seed=SEED, source_path=source)
        for name in SPLITS:
            on_disk = load_split(tmp_path / "splits" / f"{name}.jsonl")
            expected = sorted(
                (row for row in rows if assign_split(row, seed=SEED) == name),
                key=lambda row: row.id,
            )
            # Sorted by id on disk, so a diff between two runs of the pipeline
            # is a real difference rather than an ordering artefact.
            assert [row.id for row in on_disk] == [row.id for row in expected]
            assert manifest.splits[name]["rows"] == len(on_disk)
            assert manifest.counts(name)["handwritten"] + manifest.counts(name)["synthetic"] == len(
                on_disk
            )
            assert manifest.fingerprint(name) == split_fingerprint(on_disk)
        assert manifest.rows == len(rows)
        assert manifest.assignments[rows[0].id] == assign_split(rows[0], seed=SEED)

    def test_the_manifest_is_the_evidence_a_report_quotes(self, tmp_path: Path) -> None:
        rows = load_eval_set()
        source = tmp_path / "source.jsonl"
        source.write_text("", encoding="utf-8")
        manifest = write_splits(rows, out_dir=tmp_path / "splits", seed=SEED, source_path=source)
        payload = json.loads((tmp_path / "splits" / "manifest.json").read_text(encoding="utf-8"))
        assert payload["seed"] == SEED
        assert set(payload["splits"]) == set(SPLITS)
        assert manifest.fingerprint("test") == payload["splits"]["test"]["fingerprint"]


# ------------------------------------------------------------------ expansion #


class TestExpansion:
    def test_a_synthetic_row_names_its_generator_and_its_original(self) -> None:
        seed_row = query("rt-seed", "I cannot fall asleep at night", ["sleep-basics-rhythm"])
        made, dropped = augment_row(seed_row, docs=load_documents(), seed_value=SEED, index=0)
        assert dropped is None
        assert made is not None
        assert made.source == "synthetic"
        assert made.generator == GENERATOR_NAME
        assert made.seed_id == seed_row.id
        assert made.relevant_docs == seed_row.relevant_docs
        assert made.query != seed_row.query

    def test_expansion_is_a_function_of_the_seed(self) -> None:
        rows = load_eval_set()[:40]
        docs = load_documents()
        first = expand(rows, docs=docs, target=20, seed=SEED)
        second = expand(rows, docs=docs, target=20, seed=SEED)
        other = expand(rows, docs=docs, target=20, seed=SEED + 1)
        assert [row.query for row in first.synthetic()] == [row.query for row in second.synthetic()]
        assert [row.query for row in first.synthetic()] != [row.query for row in other.synthetic()]

    def test_a_synthetic_row_never_duplicates_a_real_one(self) -> None:
        rows = load_eval_set()
        docs = load_documents()
        result = expand(rows, docs=docs, target=100, seed=SEED)
        seen = {row.query.casefold() for row in rows}
        for row in result.synthetic():
            assert row.query.casefold() not in seen
            seen.add(row.query.casefold())

    def test_nothing_is_dropped_silently(self) -> None:
        rows = load_eval_set()
        result = expand(rows, docs=load_documents(), target=100, seed=SEED)
        assert all(record.get("reason") for record in result.dropped)
        assert all(record.get("seed_id") for record in result.dropped)
        assert len(result.rows) == 100

    def test_ids_are_renumbered_densely_so_a_drop_cannot_move_them(self) -> None:
        # A hole in the id sequence would renumber every later row, and the
        # split fingerprints (the "was this tuned on?" evidence) would move with
        # them. Dense ids make the file hash a function of the content alone.
        rows = load_eval_set()[:40]
        result = expand(rows, docs=load_documents(), target=10, seed=SEED)
        assert [row.id for row in result.rows] == [
            f"rt-syn-{position:04d}" for position in range(1, len(result.rows) + 1)
        ]

    def test_a_no_answer_seed_stays_a_no_answer_row(self) -> None:
        rows = [row for row in load_eval_set() if row.style == "no-answer"][:10]
        result = expand(rows, docs=load_documents(), target=10, seed=SEED)
        for row in result.synthetic():
            assert row.relevant_docs == []
            assert row.has_answer is False
            assert row.no_answer_reason

    def test_the_expanded_set_still_validates(self) -> None:
        # `Expansion.rows` is the synthetic half; the file the DVC stage writes
        # is seeds + synthetic, and that is the set the eval runs on.
        rows = load_eval_set()
        result = expand(rows, docs=load_documents(), target=25, seed=SEED)
        expanded = [*rows, *result.rows]
        report = validate_against_corpus(expanded)
        assert report["never_labelled"] == []
        assert len(expanded) == len(rows) + len(result.rows)
        assert len({row.id for row in expanded}) == len(expanded)
