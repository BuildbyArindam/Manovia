"""Reading, converting and checking the data a multilingual emotion run trains on.

Three jobs, all of them about *trusting the rows*:

1. **Load** a multi-label JSONL split into :class:`EmotionExample` objects, with
   the taxonomy enforced on the way in (an unknown label is an error, not a
   column of zeros).
2. **Convert** a source corpus (GoEmotions, EmoNoBa) through the documented
   table in :mod:`manovia_ml.training.emotion.taxonomy`, reporting what the
   conversion dropped. A conversion that quietly loses half the rows is how a
   model ends up describing a corpus nobody checked.
3. **Check** that a held-out set really is held out: exact duplicates *and*
   near-duplicates, reported with the pairs, because "train and eval share a
   sentence" is a number-inflating bug that no metric will show you.

The validation split also lives here: the hand-written en/hi/bn set in
``ml/data/validation/emotion_validation.jsonl`` is loaded through exactly the
same code path as the corpus, so it cannot drift into a different shape. Nothing
in this module downloads anything, and nothing imports a training framework.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from manovia_ml.hashing import DatasetRef, dataset_ref, read_jsonl, sha256_file
from manovia_ml.paths import resolve
from manovia_ml.training.emotion.taxonomy import (
    EMOTIONS,
    NEUTRAL,
    NEUTRAL_THRESHOLD,
    SCHEMES,
    target_weights,
    unreachable_targets,
)

__all__ = [
    "DEFAULT_VALIDATION_PATH",
    "NEAR_DUPLICATE_THRESHOLD",
    "PARAPHRASE_THRESHOLD",
    "SUPPORTED_LANGUAGES",
    "EmotionExample",
    "OverlapReport",
    "convert_source_rows",
    "default_validation_path",
    "exact_duplicate_pairs",
    "example_dataset_ref",
    "is_near_duplicate",
    "load_examples",
    "load_validation",
    "normalise_text",
    "overlap_report",
    "summary",
    "to_binary_targets",
    "to_soft_targets",
    "write_examples",
]

#: The three languages the product speaks (plus romanised Hindi, which is a
#: distinct input register rather than a language). A row in another language is
#: not rejected — a corpus may legitimately add one — but it is counted
#: separately in the summary so a run can say what it actually trained on.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "hi", "hi-Latn", "bn")

#: The held-out multilingual set. Hand-written (see ``ml/data/validation/``),
#: never trained on, used for the final metrics and the per-language table.
DEFAULT_VALIDATION_PATH = "data/validation/emotion_validation.jsonl"

#: Character n-gram size for the near-duplicate check. Three is short enough to
#: catch a re-ordered clause and long enough that two different short sentences
#: rarely collide (measured on the toy fixture: no false pairs at 0.80).
SHINGLE_SIZE = 3

#: Jaccard similarity over character shingles at or above this is a near-duplicate.
NEAR_DUPLICATE_THRESHOLD = 0.80

#: ``difflib`` ratio at or above this is a near-duplicate too — the *reworded*
#: case ("my flight got cancelled and I spent my birthday in an airport" vs "my
#: flight was cancelled so I spent my birthday in the airport") scores 0.87
#: there and only 0.55 on character shingles, because the words moved. Getting
#: this wrong is how a held-out set quietly shares its sentences with training.
PARAPHRASE_THRESHOLD = 0.85

#: Cheapest prefilter before the (quadratic) difflib call: the two texts must
#: share at least this share of their word types. Without it, checking a 5k-row
#: held-out set against 43k training rows would spend minutes in difflib.
WORD_OVERLAP_PREFILTER = 0.5


@dataclass(frozen=True)
class EmotionExample:
    """One text and the Manovia emotions it expresses.

    ``labels`` is the ground truth (a set — order-insensitive, unique).
    ``weights`` is optional: a converted corpus may keep the *soft* weight the
    mapping assigned (``annoyance`` → anger 0.6) and use it as the training
    target, while the labels stay binary for scoring. Both are always present in
    taxonomy order by the time a run sees them.
    """

    id: str
    text: str
    labels: tuple[str, ...]
    lang: str
    source: str
    weights: tuple[tuple[str, float], ...] = ()

    @property
    def label_set(self) -> frozenset[str]:
        return frozenset(self.labels)

    def weight_map(self) -> dict[str, float]:
        return dict(self.weights)

    def to_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": self.id,
            "text": self.text,
            "labels": list(self.labels),
            "lang": self.lang,
            "source": self.source,
        }
        if self.weights:
            row["weights"] = dict(self.weights)
        return row


@dataclass(frozen=True)
class OverlapReport:
    """What the train/held-out check found."""

    train_rows: int
    held_out_rows: int
    exact: tuple[tuple[str, str], ...]
    near: tuple[tuple[str, str, float], ...]

    @property
    def clean(self) -> bool:
        return not self.exact and not self.near

    def as_dict(self) -> dict[str, Any]:
        return {
            "train_rows": self.train_rows,
            "held_out_rows": self.held_out_rows,
            "exact_duplicates": len(self.exact),
            "near_duplicates": len(self.near),
            "exact_pairs": [list(pair) for pair in self.exact],
            "near_pairs": [[a, b, round(score, 4)] for a, b, score in self.near],
        }

    def fail_message(self) -> str:
        lines = [
            f"{len(self.exact)} exact duplicate(s) and {len(self.near)} near-duplicate(s) "
            "between the training data and the held-out set:"
        ]
        for left, right in self.exact[:10]:
            lines.append(f"  exact  {left} == {right}")
        for left, right, score in self.near[:10]:
            lines.append(f"  near   {left} ~ {right} (similarity {score:.3f})")
        lines.append(
            "A held-out set that shares rows with training does not measure what it claims to."
        )
        return "\n".join(lines)


def normalise_text(text: str) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace.

    Deliberately aggressive: the duplicate check is about *content*, and
    ``"Exam tomorrow!!!"`` vs ``"exam tomorrow"`` is the same row. Unicode
    normalisation matters for the Devanagari and Bengali rows, where the same
    grapheme has more than one code-point sequence.
    """

    decomposed = unicodedata.normalize("NFKC", text).casefold()
    kept = [char if (char.isalnum() or char.isspace()) else " " for char in decomposed]
    return " ".join("".join(kept).split())


def _shingles(text: str, size: int = SHINGLE_SIZE) -> set[str]:
    cleaned = normalise_text(text)
    compact = cleaned.replace(" ", "")
    if len(compact) <= size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def _jaccard(left: str, right: str) -> float:
    """Jaccard over character shingles, with a containment escape hatch."""

    left_set = _shingles(left)
    right_set = _shingles(right)
    if not left_set or not right_set:
        return 0.0
    if left_set <= right_set or right_set <= left_set:
        # One text contains the other: a validation row that is a longer version
        # of a training row leaks the same content even when Jaccard is low.
        smaller, larger = sorted((left_set, right_set), key=len)
        return max(
            len(smaller) / len(larger), len(left_set & right_set) / len(left_set | right_set)
        )
    return len(left_set & right_set) / len(left_set | right_set)


def _word_overlap(left: str, right: str) -> float:
    left_words = set(normalise_text(left).split())
    right_words = set(normalise_text(right).split())
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / len(left_words | right_words)


def _similarity(left: str, right: str) -> float:
    """How close two texts are: the larger of the two near-duplicate rules.

    Character-shingle Jaccard catches a copy with punctuation or a word changed;
    ``difflib`` catches the same content in a different order. A value at or
    above one of the two thresholds means "treat as the same row", and the
    report prints which score it was.
    """

    jaccard = _jaccard(left, right)
    if jaccard >= NEAR_DUPLICATE_THRESHOLD:
        return jaccard
    if _word_overlap(left, right) < WORD_OVERLAP_PREFILTER:
        return jaccard
    return max(
        jaccard, difflib.SequenceMatcher(None, normalise_text(left), normalise_text(right)).ratio()
    )


def is_near_duplicate(
    left: str, right: str, *, threshold: float = NEAR_DUPLICATE_THRESHOLD
) -> bool:
    """True when two texts are the same row by either rule.

    ``threshold`` applies to the character-shingle rule; the paraphrase rule has
    its own (``PARAPHRASE_THRESHOLD``). Both are documented constants so a
    reviewer can argue with a number rather than with a heuristic buried in a
    comparison.
    """

    if _jaccard(left, right) >= threshold:
        return True
    if _word_overlap(left, right) < WORD_OVERLAP_PREFILTER:
        return False
    ratio = difflib.SequenceMatcher(None, normalise_text(left), normalise_text(right)).ratio()
    return ratio >= PARAPHRASE_THRESHOLD


def exact_duplicate_pairs(
    train: Sequence[EmotionExample],
    held_out: Sequence[EmotionExample],
) -> tuple[tuple[str, str], ...]:
    """Exact duplicates (by normalised text) between two splits, as id pairs.

    Near-duplicates need the inverted index in :func:`overlap_report`; this is
    the cheap, exact half, kept separate because it is the case that must never
    happen at all.
    """

    seen: dict[str, str] = {}
    for example in train:
        seen.setdefault(normalise_text(example.text), example.id)
    return tuple(
        (seen[normalise_text(example.text)], example.id)
        for example in held_out
        if normalise_text(example.text) in seen
    )


def overlap_report(
    train: Sequence[EmotionExample],
    held_out: Sequence[EmotionExample],
    *,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> OverlapReport:
    """Exact + near-duplicate check between a training split and a held-out set.

    An **inverted index** over character shingles makes this linear in the data
    rather than quadratic: a held-out row is only compared with training rows
    that share at least one shingle with it. GoEmotions train x test is ~43k x
    5.4k rows, which is 235M pairs if written the obvious way and a few seconds
    the way it is written here. Exact duplicates are reported separately (and
    cheaply) so the common case never pays for the set arithmetic.
    """

    exact: list[tuple[str, str]] = []
    exact_keys: set[str] = set()
    by_key: dict[str, str] = {}
    index: dict[str, list[int]] = {}
    train_keys: list[str] = []
    train_sets: list[set[str]] = []
    for position, example in enumerate(train):
        key = normalise_text(example.text)
        by_key.setdefault(key, example.id)
        train_keys.append(key)
        shingles = _shingles(example.text)
        train_sets.append(shingles)
        for shingle in shingles:
            index.setdefault(shingle, []).append(position)

    for example in held_out:
        key = normalise_text(example.text)
        if key in by_key:
            exact.append((by_key[key], example.id))
            exact_keys.add(key)

    near: list[tuple[str, str, float]] = []
    for example in held_out:
        held_key = normalise_text(example.text)
        if held_key in exact_keys:
            continue
        held = _shingles(example.text)
        if not held:
            continue
        candidates: set[int] = set()
        for shingle in held:
            candidates.update(index.get(shingle, ()))
        for position in sorted(candidates):
            train_set = train_sets[position]
            if not train_set & held:
                continue
            score = _similarity(train_keys[position], held_key)
            if score >= threshold or is_near_duplicate(
                train_keys[position], held_key, threshold=threshold
            ):
                near.append((train[position].id, example.id, score))
    return OverlapReport(
        train_rows=len(train),
        held_out_rows=len(held_out),
        exact=tuple(exact),
        near=tuple(near),
    )


def _labels_of_row(row: Mapping[str, Any]) -> tuple[str, ...]:
    raw = row.get("labels", row.get("label"))
    if raw is None:
        raise ValueError(f"row {row.get('id', '<no id>')}: no `labels` or `label` field")
    if isinstance(raw, str):
        values: list[str] = [raw]
    elif isinstance(raw, Iterable):
        values = [str(item) for item in raw]
    else:
        raise ValueError(f"row {row.get('id', '<no id>')}: unsupported labels value {raw!r}")
    cleaned: list[str] = []
    for value in values:
        label = str(value).strip().casefold()
        if label and label not in cleaned:
            cleaned.append(label)
    if not cleaned:
        raise ValueError(f"row {row.get('id', '<no id>')}: empty label list")
    unknown = [label for label in cleaned if label not in EMOTIONS]
    if unknown:
        raise ValueError(
            f"row {row.get('id', '<no id>')}: label(s) {unknown} are not in Manovia's taxonomy "
            f"{list(EMOTIONS)} — map them first (see taxonomy.py)"
        )
    return tuple(label for label in EMOTIONS if label in cleaned)


def _weights_of_row(row: Mapping[str, Any]) -> tuple[tuple[str, float], ...]:
    raw = row.get("weights")
    if not isinstance(raw, Mapping):
        return ()
    pairs = []
    for label, weight in raw.items():
        key = str(label).strip().casefold()
        if key in EMOTIONS and float(weight) > 0.0:
            pairs.append((key, round(float(weight), 6)))
    return tuple((label, dict(pairs)[label]) for label in EMOTIONS if label in dict(pairs))


def load_examples(
    path: Path | str,
    *,
    source: str | None = None,
    limit: int | None = None,
) -> list[EmotionExample]:
    """Read a split into examples, checking every label against the taxonomy."""

    rows = read_jsonl(path)
    examples: list[EmotionExample] = []
    for index, row in enumerate(rows):
        text = str(row.get("text", "")).strip()
        if not text:
            raise ValueError(f"{path}: row {index} has an empty `text`")
        examples.append(
            EmotionExample(
                id=str(row.get("id", f"{Path(path).stem}-{index}")),
                text=text,
                labels=_labels_of_row(row),
                lang=str(row.get("lang", "en")).strip() or "en",
                source=source or str(row.get("source", "unknown")),
                weights=_weights_of_row(row),
            )
        )
        if limit is not None and len(examples) >= limit:
            break
    if not examples:
        raise ValueError(f"{path}: no rows")
    return examples


def write_examples(path: Path | str, examples: Sequence[EmotionExample]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for example in examples:
            handle.write(json.dumps(example.to_row(), ensure_ascii=False, sort_keys=True) + "\n")
    return target


def default_validation_path() -> Path:
    return resolve(DEFAULT_VALIDATION_PATH)


def load_validation(path: Path | str | None = None) -> list[EmotionExample]:
    """The hand-written held-out multilingual set."""

    return load_examples(
        path if path is not None else default_validation_path(), source="handwritten"
    )


def to_soft_targets(
    examples: Sequence[EmotionExample],
    labels: Sequence[str] = EMOTIONS,
) -> list[list[float]]:
    """Training targets: the mapping's weights when present, else 1.0/0.0."""

    matrix: list[list[float]] = []
    for example in examples:
        weights = example.weight_map()
        row = [1.0 if label in example.labels else 0.0 for label in labels]
        for index, label in enumerate(labels):
            # The mapping's weight *is* the target where it exists: "annoyance"
            # is 0.6 of anger, not 1.0 of it. Overriding rather than max()-ing is
            # what makes that sentence true.
            if label in weights:
                row[index] = round(weights[label], 6)
        matrix.append(row)
    return matrix


def to_binary_targets(
    examples: Sequence[EmotionExample],
    labels: Sequence[str] = EMOTIONS,
) -> list[list[int]]:
    """Scoring targets: 1 when the label is in the row's label set."""

    return [[1 if label in example.labels else 0 for label in labels] for example in examples]


def summary(examples: Sequence[EmotionExample]) -> dict[str, Any]:
    """Counts a reviewer needs: rows, languages, labels, multi-label share."""

    by_language = Counter(example.lang for example in examples)
    by_label: Counter[str] = Counter()
    for example in examples:
        for label in example.labels:
            by_label[label] += 1
    multi = sum(1 for example in examples if len(example.labels) > 1)
    return {
        "rows": len(examples),
        "languages": dict(sorted(by_language.items())),
        "labels": {label: by_label.get(label, 0) for label in EMOTIONS},
        "labels_absent": [label for label in EMOTIONS if by_label.get(label, 0) == 0],
        "multi_label_rows": multi,
        "sources": dict(sorted(Counter(example.source for example in examples).items())),
    }


def example_dataset_ref(
    path: Path | str,
    examples: Sequence[EmotionExample],
    *,
    name: str | None = None,
) -> DatasetRef:
    """A :class:`DatasetRef` whose per-label counts come from the parsed labels.

    ``hashing.dataset_ref`` counts a single ``label`` column; a multi-label file
    has a list, so its label histogram is built here and substituted in. The
    sha256 still comes from the bytes on disk.
    """

    base = dataset_ref(path, name=name, text_field="text", label_field="labels")
    counted = summary(examples)
    return replace(
        base,
        rows=len(examples),
        languages=dict(counted["languages"]),
        labels={label: count for label, count in counted["labels"].items() if count},
    )


def convert_source_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    scheme: str,
    lang: str,
    source: str | None = None,
    neutral_threshold: float = NEUTRAL_THRESHOLD,
    keep_soft_weights: bool = True,
) -> tuple[list[EmotionExample], dict[str, Any]]:
    """Convert raw source rows into Manovia-shaped examples.

    Returns the examples *and* a report of what the conversion did: rows in, rows
    with at least one target, rows dropped (no target at all), and the per-source
    label drop counts. ``neutral`` is derived by
    :func:`taxonomy.target_weights`' caller: a row with no other positive target
    becomes neutral, and a row whose only labels were dropped becomes neutral
    too — except that a row that produced *nothing* is dropped rather than
    silently labelled neutral, because ``{surprise}`` is not "no emotion", it is
    "an emotion Manovia does not have".
    """

    if scheme not in SCHEMES:
        raise KeyError(f"unknown scheme {scheme!r}; known: {sorted(SCHEMES)}")
    examples: list[EmotionExample] = []
    dropped: Counter[str] = Counter()
    rows_with_target = 0
    rows_neutral_only = 0
    unsupported = unreachable_targets(SCHEMES[scheme])

    for index, row in enumerate(rows):
        text = str(row.get("text", "")).strip()
        if not text:
            dropped["empty_text"] += 1
            continue
        raw = row.get("labels", row.get("label", []))
        if isinstance(raw, str):
            source_labels = [raw]
        elif isinstance(raw, Mapping):  # EmoNoBa-style one column per label
            source_labels = [str(key) for key, value in raw.items() if float(value) > 0]
        else:
            source_labels = [str(item) for item in raw]
        source_labels = [label.strip().casefold() for label in source_labels]
        if not source_labels:
            dropped["no_source_labels"] += 1
            continue

        weights = target_weights(source_labels, scheme=scheme)
        for label in source_labels:
            if label not in SCHEMES[scheme].table or not SCHEMES[scheme].table[label]:
                dropped[f"label:{label}"] += 1
        if not weights and not any(label == NEUTRAL for label in source_labels):
            dropped["no_target"] += 1
            continue

        positives = {
            label: weight for label, weight in weights.items() if weight >= neutral_threshold
        }
        if positives:
            labels = tuple(label for label in EMOTIONS if label in positives)
            row_weights = (
                tuple((label, positives[label]) for label in EMOTIONS if label in positives)
                if keep_soft_weights
                else ()
            )
            rows_with_target += 1
        else:
            labels = (NEUTRAL,)
            row_weights = ((NEUTRAL, 1.0),)
            rows_neutral_only += 1

        examples.append(
            EmotionExample(
                id=str(row.get("id", f"{scheme}-{index}")),
                text=text,
                labels=labels,
                lang=str(row.get("lang", lang)),
                source=source or scheme,
                weights=row_weights,
            )
        )

    report = {
        "scheme": scheme,
        "rows_in": len(rows),
        "rows_out": len(examples),
        "rows_with_emotion_target": rows_with_target,
        "rows_neutral_only": rows_neutral_only,
        "dropped": dict(sorted(dropped.items())),
        "labels_unreachable_from_this_scheme": list(unsupported),
    }
    return examples, report


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect emotion data before a run.")
    sub = parser.add_subparsers(dest="command", required=True)

    summary_parser = sub.add_parser("summary", help="print row/label/language counts")
    summary_parser.add_argument("path")

    overlap_parser = sub.add_parser(
        "overlap", help="exact + near-duplicate check between a train split and a held-out set"
    )
    overlap_parser.add_argument("train")
    overlap_parser.add_argument("held_out")
    overlap_parser.add_argument("--threshold", type=float, default=NEAR_DUPLICATE_THRESHOLD)
    overlap_parser.add_argument("--json", action="store_true")

    convert_parser = sub.add_parser("convert", help="convert source rows through the mapping table")
    convert_parser.add_argument("path", help="JSONL of source rows")
    convert_parser.add_argument("--scheme", required=True, choices=sorted(SCHEMES))
    convert_parser.add_argument("--lang", required=True)
    convert_parser.add_argument("--out", required=True)
    convert_parser.add_argument("--json", action="store_true")

    hash_parser = sub.add_parser("hash", help="sha256 + row counts for a split")
    hash_parser.add_argument("path")

    args = parser.parse_args(argv)

    if args.command == "summary":
        examples = load_examples(args.path)
        print(json.dumps(summary(examples), indent=2, sort_keys=True))
        return 0

    if args.command == "hash":
        ref = dataset_ref(args.path, text_field="text", label_field="labels")
        print(json.dumps({"path": ref.path, "sha256": ref.sha256, "rows": ref.rows}, indent=2))
        return 0

    if args.command == "overlap":
        train = load_examples(args.train)
        held_out = load_examples(args.held_out)
        report = overlap_report(train, held_out, threshold=args.threshold)
        # Printed either way: this JSON *is* the human-readable answer (which rows
        # collided, and how closely). `--json` exists for symmetry with the other
        # subcommands and for scripts that pipe the output.
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
        if not report.clean:
            print(report.fail_message(), file=sys.stderr)
            return 1
        return 0

    rows = read_jsonl(args.path)
    examples, conversion = convert_source_rows(rows, scheme=args.scheme, lang=args.lang)
    write_examples(args.out, examples)
    conversion["out"] = str(args.out)
    conversion["sha256"] = sha256_file(args.out)
    print(json.dumps(conversion, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(_main())
