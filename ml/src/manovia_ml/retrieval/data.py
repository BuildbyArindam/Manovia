"""The retrieval eval set: schema, validation, and the frozen four-way split.

One JSONL file of hand-written, hand-labelled user queries
(``ml/data/retrieval/retrieval_queries.jsonl``) is the ground truth. Everything
else in this module exists to keep that file honest and its use reproducible:

* **The schema rejects the mistakes a labelled IR set actually makes.** A
  ``synthetic`` row without a generator and a seed id, an ``has_answer: false``
  row that still names documents, a label that is not a document id in the
  corpus, two rows sharing an id — all errors at load time, not surprises in a
  report.
* **The split is a pure function of (row id, seed).** No RNG state, no file
  order, no floating point: ``assign_split`` hashes ``seed:id`` and reads a
  bucket. Two people on two machines get the same split, and ``dvc repro`` can
  prove it against ``dvc.lock``.
* **The test split is hand-written only, and nothing tunes on it.** Synthetic
  (template-paraphrased) rows can land in train, dev or calibration; they can
  never land in test, because a paraphrase of a seed query is closer to that
  seed than a real user's wording is, and a number measured on paraphrases
  overstates the system. The fusion weight is fitted on ``dev``, the abstain
  thresholds on ``calibration``, and ``test`` is read by the evaluator alone.
* **Every split is hashed.** ``manifest.json`` records the sha256 of each split
  file plus its row ids, so "the test queries were not used for tuning" is a
  checkable statement (``split_fingerprint``) rather than a promise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from manovia_ml.config import StrictModel
from manovia_ml.hashing import DatasetRef, dataset_ref, read_jsonl, sha256_file
from manovia_ml.paths import data_dir, resolve
from manovia_ml.retrieval.corpus import load_documents

__all__ = [
    "LANGUAGES",
    "SOURCES",
    "SPLITS",
    "STYLES",
    "EvalSetError",
    "RetrievalQuery",
    "SplitManifest",
    "assign_split",
    "eval_set_ref",
    "load_eval_set",
    "load_split",
    "split_fingerprint",
    "split_rows",
    "write_splits",
]

#: The chat surface's languages, with the two romanised forms the eval set
#: covers separately: Hinglish (``hi-Latn``) and romanised Bengali (``bn-Latn``)
#: are not "Hindi" or "Bengali" to a tokenizer, and a retriever that mixes them
#: up is measurably worse on them.
LANGUAGES: tuple[str, ...] = ("en", "hi", "hi-Latn", "bn", "bn-Latn")

#: Query shapes the set deliberately covers. ``vague`` and ``no-answer`` are
#: the two that a lexical baseline cannot survive, which is the point of
#: labelling them: they are where the numbers have to move.
STYLES: tuple[str, ...] = (
    "direct",
    "vague",
    "scenario",
    "keyword",
    "hinglish",
    "romanised",
    "no-answer",
)

#: Where a row came from. ``handwritten`` rows were authored and label-reviewed
#: by a person in this repository's history; ``synthetic`` rows are
#: label-preserving paraphrases produced by ``retrieval.augment`` and must carry
#: both a generator name and the seed row they came from.
SOURCES: tuple[str, ...] = ("handwritten", "synthetic")

#: Styles a romanised row may carry (see ``RetrievalQuery._consistent``).
_ROMANISED_STYLES = frozenset({"hinglish", "romanised", "keyword", "no-answer"})

#: The four slices. ``train`` builds the contrastive pairs, ``dev`` tunes the
#: fusion weight, ``calibration`` fits the abstain thresholds, ``test`` is only
#: ever scored.
SPLITS: tuple[str, ...] = ("train", "dev", "calibration", "test")

#: Bucket ranges (out of 100) for hand-written rows: test 35, dev 25,
#: calibration 15, train 25. Test is the largest slice on purpose — it is the
#: only one a headline number comes from.
_HANDWRITTEN_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("test", 0, 35),
    ("dev", 35, 60),
    ("calibration", 60, 75),
    ("train", 75, 100),
)

#: Synthetic rows: half to dev (so tuning sees paraphrased wording too), half
#: to train (so contrastive pairs outnumber eval rows). Never test, never
#: calibration — a threshold fitted on paraphrases of its own eval rows is a
#: threshold fitted on nothing.
_SYNTHETIC_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("dev", 0, 50),
    ("train", 50, 100),
)

#: The committed ground-truth file, relative to the ML root.
EVAL_SET_RELPATH = Path("data") / "retrieval" / "retrieval_queries.jsonl"
#: Where ``write_splits`` puts the derived slices (gitignored; DVC-tracked).
SPLIT_DIR_RELPATH = Path("data") / "retrieval_splits"


class EvalSetError(ValueError):
    """The eval set cannot be trusted as ground truth."""


class RetrievalQuery(StrictModel):
    """One labelled query: the text, its language, and the documents it deserves."""

    id: str = Field(min_length=3, max_length=40, pattern=r"^rt-[a-z0-9-]+$")
    query: str = Field(min_length=2, max_length=400)
    lang: Literal["en", "hi", "hi-Latn", "bn", "bn-Latn"]
    style: Literal["direct", "vague", "scenario", "keyword", "hinglish", "romanised", "no-answer"]
    #: Every document that would genuinely help this person, best first. Empty
    #: means "the corpus has nothing for this" — a real and common answer.
    relevant_docs: list[str] = Field(default_factory=list, max_length=6)
    has_answer: bool = True
    source: Literal["handwritten", "synthetic"] = "handwritten"
    generator: str | None = None
    seed_id: str | None = None
    #: A synthetic row is only usable once a person has read it. The augment
    #: stage writes ``false``; the spot-check step in the dataset card flips it.
    spot_checked: bool = True
    #: Required when ``has_answer`` is false: why the corpus has nothing
    #: (out-of-scope, a corpus gap, or the crisis path owns the turn).
    no_answer_reason: str | None = None
    note: str = ""

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped

    @field_validator("relevant_docs")
    @classmethod
    def _unique_docs(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("relevant_docs contains a duplicate id")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> RetrievalQuery:
        if self.has_answer != bool(self.relevant_docs):
            raise ValueError(
                f"{self.id}: has_answer={self.has_answer} disagrees with "
                f"{len(self.relevant_docs)} relevant_docs"
            )
        if not self.has_answer and not (self.no_answer_reason or "").strip():
            raise ValueError(f"{self.id}: a no-answer row needs no_answer_reason")
        if self.has_answer and self.no_answer_reason:
            raise ValueError(f"{self.id}: an answerable row must not carry no_answer_reason")
        if self.source == "synthetic":
            if not self.generator or not self.seed_id:
                raise ValueError(f"{self.id}: a synthetic row needs generator and seed_id")
        elif self.generator or self.seed_id:
            raise ValueError(f"{self.id}: a handwritten row must not claim a generator/seed")
        if self.style == "no-answer" and self.has_answer:
            raise ValueError(f"{self.id}: style 'no-answer' requires has_answer=false")
        if self.lang == "hi-Latn" and self.style not in _ROMANISED_STYLES:
            # Not a hard rule about wording, a rule about labelling: a
            # romanised-Hindi row is in the set *because* it is code-mixed, and
            # a "direct" one would be an English row with a Hindi id. The two
            # shape labels a truncating paraphrase can produce ("keyword",
            # "no-answer") are allowed, since they describe the wording, not
            # the language.
            raise ValueError(
                f"{self.id}: hi-Latn rows are labelled 'hinglish' (or romanised/keyword/"
                f"no-answer), got style={self.style!r}"
            )
        if self.lang == "bn-Latn" and self.style not in _ROMANISED_STYLES - {"hinglish"}:
            raise ValueError(
                f"{self.id}: bn-Latn rows are labelled 'romanised' (or keyword/no-answer), "
                f"got style={self.style!r}"
            )
        return self


def eval_set_ref(path: Path | str, *, rows: list[dict[str, Any]] | None = None) -> DatasetRef:
    """The eval set as a :class:`~manovia_ml.hashing.DatasetRef`, for run logs.

    The generic builder defaults to a ``text``/``label`` corpus; the eval set's
    text column is ``query`` and its label is whether the corpus answers it. One
    helper means the reference recorded by the encoder stage, the reranker stage
    and the evaluation are the same object — same hash, same row count, same
    per-language counts — so two runs can be compared by their data refs.
    """

    return dataset_ref(
        path,
        name="retrieval-eval-set",
        text_field="query",
        label_field="has_answer",
        rows=rows,
    )


def load_eval_set(
    path: Path | str | None = None, *, corpus_ids: Iterable[str] | None = None
) -> list[RetrievalQuery]:
    """Read and validate the eval set.

    ``corpus_ids`` is checked against every label when given: a query pointing
    at a document that does not exist would score as a permanent miss and read
    as a retrieval bug.
    """

    file_path = resolve(path) if path is not None else resolve(EVAL_SET_RELPATH)
    rows = read_jsonl(file_path)
    queries: list[RetrievalQuery] = []
    seen: set[str] = set()
    for row in rows:
        try:
            query = RetrievalQuery.model_validate(row)
        except ValidationError as exc:
            raise EvalSetError(
                f"{file_path}: {exc.errors()[0]['msg']} ({row.get('id')!r})"
            ) from exc
        if query.id in seen:
            raise EvalSetError(f"{file_path}: duplicate query id {query.id!r}")
        seen.add(query.id)
        queries.append(query)
    if not queries:
        raise EvalSetError(f"{file_path}: the eval set is empty")

    if corpus_ids is not None:
        known = set(corpus_ids)
        unknown = sorted({doc for query in queries for doc in query.relevant_docs} - known)
        if unknown:
            raise EvalSetError(
                f"{file_path}: {len(unknown)} label(s) name documents that are not in the "
                f"corpus: {', '.join(unknown[:5])}"
            )
    return queries


def assign_split(query: RetrievalQuery, *, seed: int) -> str:
    """The split a row belongs to: a pure function of its id and the seed."""

    bucket = _bucket(query.id, seed=seed)
    ranges = _SYNTHETIC_BUCKETS if query.source == "synthetic" else _HANDWRITTEN_BUCKETS
    for name, low, high in ranges:
        if low <= bucket < high:
            return name
    return ranges[-1][0]  # pragma: no cover - ranges always cover 0..100


def _bucket(row_id: str, *, seed: int) -> int:
    digest = hashlib.sha256(f"{seed}:{row_id}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def split_rows(queries: Sequence[RetrievalQuery], *, seed: int) -> dict[str, list[RetrievalQuery]]:
    """Group rows into the four splits, each sorted by id."""

    grouped: dict[str, list[RetrievalQuery]] = {name: [] for name in SPLITS}
    for query in queries:
        grouped[assign_split(query, seed=seed)].append(query)
    for rows in grouped.values():
        rows.sort(key=lambda row: row.id)
    return grouped


def split_fingerprint(queries: Sequence[RetrievalQuery]) -> str:
    """sha256 of a split's (id, query, labels) — the "was this used for
    tuning?" evidence.

    Hashing the *content* rather than the file bytes means the fingerprint
    survives a formatting change (a re-sorted key, an added note) while still
    moving the moment a query, a label or a membership changes.
    """

    digest = hashlib.sha256()
    for query in sorted(queries, key=lambda row: row.id):
        payload = json.dumps(
            {
                "id": query.id,
                "query": query.query,
                "lang": query.lang,
                "relevant_docs": query.relevant_docs,
                "has_answer": query.has_answer,
                "source": query.source,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        digest.update(payload.encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


class SplitManifest(StrictModel):
    """What ``write_splits`` recorded: the evidence a report quotes."""

    seed: int
    source_path: str
    source_sha256: str
    rows: int
    splits: dict[str, dict[str, Any]]
    #: id → split, so "which split was this query in?" never needs a re-hash.
    assignments: dict[str, str] = Field(default_factory=dict)

    def fingerprint(self, split: str) -> str:
        return str(self.splits[split]["fingerprint"])

    def counts(self, split: str) -> dict[str, int]:
        raw = self.splits[split]
        return {str(key): int(value) for key, value in raw["counts"].items()}


def write_splits(
    queries: Sequence[RetrievalQuery], *, out_dir: Path, seed: int, source_path: Path
) -> SplitManifest:
    """Write ``<split>.jsonl`` + ``manifest.json`` into ``out_dir``."""

    out_dir.mkdir(parents=True, exist_ok=True)
    grouped = split_rows(queries, seed=seed)
    manifest_splits: dict[str, dict[str, Any]] = {}
    assignments: dict[str, str] = {}
    for name in SPLITS:
        rows = grouped[name]
        path = out_dir / f"{name}.jsonl"
        path.write_text(
            "".join(
                json.dumps(row.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
        for row in rows:
            assignments[row.id] = name
        manifest_splits[name] = {
            "path": path.name,
            "rows": len(rows),
            "sha256": sha256_file(path),
            "fingerprint": split_fingerprint(rows),
            "counts": _counts(rows),
        }
    manifest = SplitManifest(
        seed=seed,
        source_path=str(source_path),
        source_sha256=sha256_file(source_path),
        rows=len(queries),
        splits=manifest_splits,
        assignments=assignments,
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _counts(rows: Sequence[RetrievalQuery]) -> dict[str, int]:
    counts: dict[str, int] = {
        "handwritten": 0,
        "synthetic": 0,
        "answerable": 0,
        "no_answer": 0,
    }
    for row in rows:
        counts[row.source] += 1
        counts["answerable" if row.has_answer else "no_answer"] += 1
        counts[f"lang:{row.lang}"] = counts.get(f"lang:{row.lang}", 0) + 1
    return dict(sorted(counts.items()))


def load_split(path: Path | str) -> list[RetrievalQuery]:
    """Read one written split file back (used by the evaluator and the tests)."""

    rows = read_jsonl(path)
    try:
        return [RetrievalQuery.model_validate(row) for row in rows]
    except ValidationError as exc:
        raise EvalSetError(f"{path}: {exc.errors()[0]['msg']}") from exc


def validate_against_corpus(queries: Sequence[RetrievalQuery]) -> dict[str, Any]:
    """Cross-check the eval set against the live corpus; a report includes this.

    Returns the corpus size, the documents that are never labelled (a coverage
    gap worth knowing about), and the labels that no longer exist (an error).
    """

    docs = load_documents()
    known = {doc.id for doc in docs}
    labelled = {doc for query in queries for doc in query.relevant_docs}
    unknown = sorted(labelled - known)
    if unknown:
        raise EvalSetError(f"labels name documents missing from the corpus: {', '.join(unknown)}")
    return {
        "corpus_docs": len(docs),
        "labelled_docs": len(labelled),
        "never_labelled": sorted(known - labelled),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m manovia_ml.retrieval.data`` — write the splits + manifest."""

    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--src",
        default=None,
        help="eval-set JSONL (default: the expanded set, else the hand-written one)",
    )
    parser.add_argument("--out-dir", default=str(SPLIT_DIR_RELPATH))
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--check-corpus", action="store_true", help="also validate the labels")
    args = parser.parse_args(argv)

    src = resolve(args.src) if args.src else _default_source()
    rows = read_jsonl(src)
    try:
        queries = [RetrievalQuery.model_validate(row) for row in rows]
    except ValidationError as exc:
        raise EvalSetError(f"{src}: {exc.errors()[0]['msg']}") from exc
    ids = [query.id for query in queries]
    if len(set(ids)) != len(ids):
        raise EvalSetError(f"{src}: duplicate query ids")

    if args.check_corpus:
        info = validate_against_corpus(queries)
        print(
            f"corpus: {info['corpus_docs']} docs, {info['labelled_docs']} labelled, "
            f"{len(info['never_labelled'])} never labelled"
        )

    out_dir = resolve(args.out_dir)
    manifest = write_splits(queries, out_dir=out_dir, seed=args.seed, source_path=src)
    for name in SPLITS:
        entry = manifest.splits[name]
        counts = entry["counts"]
        print(
            f"{name:12s} rows={entry['rows']:4d} handwritten={counts['handwritten']:4d} "
            f"synthetic={counts['synthetic']:4d} no_answer={counts['no_answer']:3d} "
            f"sha256={entry['sha256'][:16]}… fingerprint={entry['fingerprint'][:16]}…"
        )
    print(f"manifest: {out_dir / 'manifest.json'}")
    return 0


def _default_source() -> Path:
    """The expanded set when the augment stage has run, else the handwritten one."""

    expanded = data_dir() / "retrieval" / "expanded.jsonl"
    if expanded.is_file():
        return expanded
    return data_dir() / "retrieval" / "retrieval_queries.jsonl"


if __name__ == "__main__":  # pragma: no cover - exercised through the DVC stage
    raise SystemExit(main())
