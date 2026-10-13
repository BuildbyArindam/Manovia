"""Loading, splitting and identity for the synthetic safety corpus.

The corpus is built in three layers, and this module is the one that knows all
three:

* **Seeds** — the hand-written Day-9 cases (``evals/datasets/crisis_cases_*.jsonl``).
  Read-only inputs. The *train* and *dev* seeds are expanded; the **test** split
  is never read here at all — it is read by ``evaluate.py`` and by nothing else,
  which is what "held out untouched" means in practice.
* **Expanded rows** — the output of :mod:`manovia_ml.training.safety.augment`,
  carrying a ``provenance`` block per row.
* **Splits** — ``train`` (fit), ``dev`` (model selection: early stopping, the
  level thresholds), ``calibration`` (temperature scaling and the conformal
  quantile) and ``test`` (the frozen Day-9 split, scored by the evaluator).

Splitting is stratified by (label, language) and seeded, so no level or language
can vanish from a split by accident and the same bytes always give the same
partition.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from manovia_ml.hashing import DatasetRef, sha256_file
from manovia_ml.paths import ml_root, resolve
from manovia_ml.seeds import derive_seed, seeded_rng
from manovia_ml.training.safety.augment import GeneratedRow, RulesVerdict, iter_jsonl

__all__ = [
    "SPLIT_NAMES",
    "SafetyExample",
    "assign_splits",
    "backend_rules_filter",
    "dataset_ref",
    "example_dataset_ref",
    "load_examples",
    "load_seed_split",
    "near_duplicate_report",
    "rows_to_examples",
    "summary",
    "write_examples",
    "write_manifest",
]

#: The four splits an expanded corpus is cut into, in pipeline order.
SPLIT_NAMES: Final[tuple[str, ...]] = ("train", "dev", "calibration", "test")

#: Train / dev / calibration proportions of the *expanded* rows. The test split
#: is not cut here — it is the frozen Day-9 file.
TRAIN_SHARE: Final[float] = 0.8
DEV_SHARE: Final[float] = 0.1


@dataclass(frozen=True)
class SafetyExample:
    """One labelled message, in the shape the trainer and the evaluator want."""

    id: str
    text: str
    label: str  # one of manovia_ml.training.safety.taxonomy.LEVELS
    lang: str
    category: str = "none"
    notes: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "text": self.text,
            "lang": self.lang,
            "label": self.label,
            "category": self.category,
            "notes": self.notes,
        }
        if self.provenance:
            payload["provenance"] = dict(self.provenance)
        return payload


# --------------------------------------------------------------------------- #
# Loading                                                                      #
# --------------------------------------------------------------------------- #


def load_seed_split(path: str | Path) -> list[dict[str, Any]]:
    """Read a Day-9 split file as raw dicts (the expander's input).

    Relative paths resolve against the **repository** root, not the ML root: the
    seeds live in ``evals/datasets/``, which is deliberately outside ``ml/`` so
    that the frozen splits stay where the eval harness and its hash checks expect
    them.
    """
    return list(iter_jsonl(_repo_path(path)))


def _repo_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return (ml_root().parent / candidate).resolve()


def rows_to_examples(rows: Iterable[GeneratedRow]) -> list[SafetyExample]:
    """Generated rows -> examples, keeping the provenance block attached."""
    return [
        SafetyExample(
            id=row.id,
            text=row.text,
            label=row.label,
            lang=row.lang,
            category=row.category,
            notes=row.notes,
            provenance=row.as_dict().get("provenance", {}),
        )
        for row in rows
    ]


def load_examples(path: str | Path) -> list[SafetyExample]:
    """Read a split written by :func:`write_examples`."""
    out: list[SafetyExample] = []
    for payload in iter_jsonl(resolve(path)):
        out.append(
            SafetyExample(
                id=str(payload.get("id", "")),
                text=str(payload["text"]),
                label=str(payload["label"]).strip().lower(),
                lang=str(payload.get("lang", "en")),
                category=str(payload.get("category", "none")),
                notes=str(payload.get("notes", "")),
                provenance=payload.get("provenance", {}) or {},
            )
        )
    return out


def write_examples(path: str | Path, examples: Sequence[SafetyExample]) -> Path:
    """Write one split as JSONL (deterministic: the list order is preserved)."""
    target = resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return target


# --------------------------------------------------------------------------- #
# The rules-engine bridge                                                      #
# --------------------------------------------------------------------------- #


def backend_rules_filter() -> Any:
    """The Day-8 rules engine as a ``(text, lang) -> RulesVerdict`` callable.

    Imported lazily and *inside* this function on purpose: ``ml/`` must install
    and run without the backend checkout, and the offline catalogue/docs path has
    no reason to load a regex engine. When the backend is absent the caller gets
    ``None`` and the augmentation reports ``rules_filter: unavailable`` rather
    than pretending it filtered anything.
    """
    import sys

    backend = ml_root().parent / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    try:
        from app.services.safety.rules import build_engine
    except Exception:  # pragma: no cover - backend not importable
        return None

    engine = build_engine()

    def assess(text: str, lang: str) -> RulesVerdict:
        assessment = engine.assess(text, language=lang)
        return RulesVerdict(
            level=assessment.level.name.lower(),
            categories=tuple(item.value for item in assessment.matched_categories),
            negated_hits=assessment.context.negated_hits,
            figurative_hits=assessment.context.figurative_hits,
        )

    return assess


# --------------------------------------------------------------------------- #
# Splitting                                                                    #
# --------------------------------------------------------------------------- #


def assign_splits(
    examples: Sequence[SafetyExample],
    *,
    seed: int = 20261013,
    train_share: float = TRAIN_SHARE,
    dev_share: float = DEV_SHARE,
    test_share: float = 0.0,
) -> dict[str, list[SafetyExample]]:
    """Stratified train / dev / calibration partition of the expanded rows.

    Every (label, language) stratum is shuffled with its own derived seed and cut
    at the same proportions, so a small stratum cannot be swallowed whole by the
    training split and a rare level cannot disappear.

    ``test_share`` is 0.0 for the real corpus, and that is the point: the held-out
    split for the safety model is the **frozen Day-9 file**, produced by nobody's
    splitter. It is non-zero only for the hand-written toy fixture under
    ``ml/data/toy/``, where a fourth slice is needed to exercise the same code
    path offline — and the smoke config that uses it says so.
    """
    if not 0 < train_share < 1:
        raise ValueError("train_share must be in (0, 1)")
    if dev_share < 0 or test_share < 0:
        raise ValueError("dev_share and test_share must be >= 0")
    if train_share + dev_share + test_share >= 1:
        raise ValueError("train_share + dev_share + test_share must stay below 1")
    strata: dict[tuple[str, str], list[SafetyExample]] = {}
    for example in examples:
        strata.setdefault((example.label, example.lang), []).append(example)

    names = ["train", "dev", "calibration"] + (["test"] if test_share > 0 else [])
    splits: dict[str, list[SafetyExample]] = {name: [] for name in names}
    shares = [train_share, dev_share, 1.0 - train_share - dev_share - test_share] + (
        [test_share] if test_share > 0 else []
    )
    for key in sorted(strata):
        group = list(strata[key])
        seeded_rng(seed, "split", *key).shuffle(group)
        n = len(group)
        cursor = 0
        for position, share in enumerate(shares):
            remaining = n - cursor
            if position == len(shares) - 1:
                take = remaining
            elif remaining <= 1:
                take = 0
            else:
                take = max(1, round(remaining * share)) if n >= 3 else 0
                take = min(take, max(0, remaining - (len(shares) - position - 1)))
            splits[names[position]].extend(group[cursor : cursor + take])
            cursor += take

    for name in splits:
        splits[name].sort(key=lambda example: example.id)
    return splits


# --------------------------------------------------------------------------- #
# Identity and leakage                                                         #
# --------------------------------------------------------------------------- #


def dataset_ref(name: str, path: str | Path) -> DatasetRef:
    """A :class:`DatasetRef` for one split file: hash, rows, language and label counts."""
    target = resolve(path)
    examples = load_examples(target)
    return DatasetRef(
        name=name,
        path=_display(target),
        sha256=sha256_file(target),
        rows=len(examples),
        languages=dict(sorted(Counter(example.lang for example in examples).items())),
        labels=dict(sorted(Counter(example.label for example in examples).items())),
    )


def example_dataset_ref(name: str, examples: Sequence[SafetyExample],
    path: str | Path) -> DatasetRef:
    """:func:`dataset_ref` from an in-memory list, for rows not yet written."""
    return DatasetRef(
        name=name,
        path=_display(resolve(path)),
        sha256=sha256_file(resolve(path)),
        rows=len(examples),
        languages=dict(sorted(Counter(example.lang for example in examples).items())),
        labels=dict(sorted(Counter(example.label for example in examples).items())),
    )


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ml_root()))
    except ValueError:
        return str(path)


def _shingles(text: str, size: int = 3) -> frozenset[str]:
    normalised = " ".join(text.casefold().split())
    if len(normalised) <= size:
        return frozenset({normalised})
    spans = range(len(normalised) - size + 1)
    return frozenset(normalised[index : index + size] for index in spans)


def near_duplicate_report(
    left: Sequence[SafetyExample],
    right: Sequence[SafetyExample],
    *,
    threshold: float = 0.8,
) -> dict[str, Any]:
    """Exact and near-duplicate overlap between two sets of examples.

    Used by the evaluator to answer "did any training row leak into the test
    split?" — the question a frozen split exists to make answerable. Returns
    statistics only: no text crosses the boundary in either direction, so the
    check cannot itself become a leak.
    """
    left_keys = {" ".join(example.text.casefold().split()) for example in left}
    right_keys = {" ".join(example.text.casefold().split()) for example in right}
    exact = left_keys & right_keys

    right_shingles = [_shingles(example.text) for example in right]
    worst = 0.0
    worst_id = ""
    over_threshold = 0
    for example in left:
        shingles = _shingles(example.text)
        for other, other_shingles in zip(right, right_shingles, strict=False):
            union = len(shingles | other_shingles)
            similarity = (len(shingles & other_shingles) / union) if union else 0.0
            if similarity > worst:
                worst = similarity
                worst_id = f"{example.id} ~ {other.id}"
            if similarity >= threshold:
                over_threshold += 1
                break
    return {
        "left_rows": len(left),
        "right_rows": len(right),
        "exact_overlap": len(exact),
        "near_duplicate_threshold": threshold,
        "near_duplicate_rows": over_threshold,
        "max_jaccard": round(worst, 4),
        "max_jaccard_pair": worst_id,
    }


# --------------------------------------------------------------------------- #
# Summaries and manifests                                                      #
# --------------------------------------------------------------------------- #


def summary(examples: Sequence[SafetyExample]) -> dict[str, Any]:
    """Label, language, category and provenance counts for one split."""
    sources: Counter[str] = Counter()
    for example in examples:
        provenance = example.provenance or {}
        sources[str(provenance.get("source", "unknown"))] += 1
    return {
        "rows": len(examples),
        "labels": dict(sorted(Counter(example.label for example in examples).items())),
        "languages": dict(sorted(Counter(example.lang for example in examples).items())),
        "categories": dict(sorted(Counter(example.category for example in examples).items())),
        "provenance_sources": dict(sorted(sources.items())),
    }


def write_manifest(path: str | Path, payload: Mapping[str, Any]) -> Path:
    """Write the split manifest (hashes, counts, seeds, generator versions)."""
    target = resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def read_manifest(path: str | Path) -> dict[str, Any]:
    payload = json.loads(resolve(path).read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def derive_split_seed(seed: int, name: str) -> int:
    """A sub-seed for one split, so adding a split cannot move another's rows."""
    return derive_seed(seed, "safety-split", name)


def iter_example_dicts(examples: Iterator[SafetyExample]) -> Iterable[dict[str, Any]]:
    """Adapter for callers that want the JSON payloads, not the dataclasses."""
    return [example.as_dict() for example in examples]
