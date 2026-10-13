"""The toy emotion dataset: hand-written, deterministic, deliberately tiny.

Why this exists: the smoke run and the reproducibility tests need *some* data,
and every honest option was worse. A real corpus is a network download with a
licence question attached (see ``ml/data/CATALOGUE.md``), and a random fixture
generated from templates would make "the same seed reproduces the same metrics"
a statement about the generator rather than about training.

So: 84 short everyday sentences, four language variants (en, hi, hi-Latn, bn) by
six emotions (joy, sadness, anger, fear, calm, neutral), written by the same
person who wrote this file. No
personal data, no crisis content, no method or means anywhere in it (the
Manovia anxiety is about an exam, not about self-harm — a fixture that drifts
into crisis language would end up in a smoke run that mocks the safety path).

The split is the DVC stage (``ml/dvc.yaml``): :func:`build_splits` reads the
fixture, stratifies by label with a seed-derived shuffle, and writes
``train``/``eval`` JSONL plus a manifest with the sha256 of every file involved.
Every output is a pure function of the fixture bytes, the seed and the fraction,
so ``dvc repro`` on another machine either reproduces the recorded hashes or
fails loudly.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manovia_ml.hashing import (
    DatasetRef,
    check_expected,
    dataset_ref,
    read_jsonl,
    sha256_file,
    sha256_text,
)
from manovia_ml.paths import data_dir, resolve
from manovia_ml.seeds import seeded_rng

__all__ = [
    "FIXTURE_PATH",
    "GENERATOR_VERSION",
    "TOY_LABELS",
    "SplitManifest",
    "build_splits",
    "default_fixture_path",
    "load_fixture",
    "split_rows",
]

GENERATOR_VERSION = "1"

#: Explicit order. `sorted()` would give the same thing here, but the point of
#: this tuple is that changing the class order is a deliberate edit with a
#: visible diff, not a side effect of renaming a label.
TOY_LABELS: tuple[str, ...] = ("joy", "sadness", "anger", "fear", "calm", "neutral")

FIXTURE_PATH = "data/toy/toy_emotion.jsonl"
DEFAULT_OUT_DIR = "data/processed"


def default_fixture_path() -> Path:
    return data_dir() / "toy" / "toy_emotion.jsonl"


def load_fixture(path: Path | str | None = None) -> list[dict[str, Any]]:
    """Read and sanity-check the fixture, failing on anything unexpected."""

    rows = read_jsonl(path or default_fixture_path())
    if not rows:
        raise ValueError(f"{path or default_fixture_path()}: fixture is empty")
    seen_ids: set[str] = set()
    for row in rows:
        missing = {"id", "text", "label", "lang"} - set(row)
        if missing:
            raise ValueError(f"fixture row {row.get('id', '<no id>')}: missing {sorted(missing)}")
        if row["label"] not in TOY_LABELS:
            raise ValueError(f"fixture row {row['id']}: unknown label {row['label']!r}")
        if row["id"] in seen_ids:
            raise ValueError(f"fixture row {row['id']}: duplicate id")
        seen_ids.add(str(row["id"]))
    return rows


def split_rows(
    rows: list[dict[str, Any]],
    *,
    seed: int,
    train_fraction: float,
    labels: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Stratify by label, shuffle within each label, split, then sort by id.

    Stratified on purpose: with 14 rows per class, an unstratified split can
    leave a class out of a split entirely, which turns "accuracy" on 84 rows
    into a coin flip and the reproducibility test into noise.
    """

    if not 0.0 < train_fraction < 1.0:
        raise ValueError(f"train_fraction must be in (0, 1), got {train_fraction}")

    active = list(labels) if labels else list(TOY_LABELS)
    unknown = [label for label in active if label not in TOY_LABELS]
    if unknown:
        raise ValueError(f"unknown labels: {unknown}; known: {list(TOY_LABELS)}")
    if len(active) < 2:
        raise ValueError("need at least two labels to train a classifier")

    train: list[dict[str, Any]] = []
    evaluation: list[dict[str, Any]] = []

    for offset, label in enumerate(active):
        group = sorted(
            (row for row in rows if row["label"] == label), key=lambda row: str(row["id"])
        )
        rng = seeded_rng(seed, "split", label, str(offset))
        rng.shuffle(group)
        n_train = max(1, min(len(group) - 1, round(len(group) * train_fraction)))
        train.extend(group[:n_train])
        evaluation.extend(group[n_train:])

    key = lambda row: str(row["id"])  # noqa: E731 - one expression, used twice
    return (
        sorted(train, key=key),
        sorted(evaluation, key=key),
    )


@dataclass(frozen=True)
class SplitManifest:
    """The record DVC hashes and the training run quotes."""

    source: DatasetRef
    train: DatasetRef
    eval: DatasetRef
    seed: int
    train_fraction: float
    labels: tuple[str, ...] = ()
    prefix: str = "toy"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "toy_split",
            "generator": "manovia_ml.data.toy",
            "generator_version": GENERATOR_VERSION,
            "source": self.source.as_dict(),
            "seed": self.seed,
            "train_fraction": self.train_fraction,
            "labels": list(self.labels),
            "prefix": self.prefix,
            "splits": {"train": self.train.as_dict(), "eval": self.eval.as_dict()},
        }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_splits(
    *,
    source_path: Path | str | None = None,
    out_dir: Path | str = DEFAULT_OUT_DIR,
    seed: int,
    train_fraction: float = 0.7,
    labels: Sequence[str] | None = None,
    prefix: str | None = None,
) -> SplitManifest:
    """Write the prefixed splits and their manifest.

    ``labels`` restricts the split to a subset of the six classes. The smoke
    config uses a two-class slice because a binary problem on twenty rows is
    *readable*: every eval row carries one of two labels, so the majority-class
    floor is 0.500 and the numbers can be compared with it. On the six-class
    slice at this size accuracy sits at chance and means nothing.

    Neither slice is a quality benchmark — every row is a distinct hand-written
    sentence, so held-out rows share only incidental vocabulary with training
    rows and the measured accuracy is below the majority-class floor. What the
    smoke slice exists to prove is reproducibility (bit-identical metrics for a
    fixed config and seed) and that training still fits its own rows. The
    measurements are in ml/findings/2026-10-13-toy-slice-baselines.md.
    """

    source = Path(source_path) if source_path is not None else default_fixture_path()
    rows = load_fixture(source)
    active = tuple(labels) if labels else tuple(TOY_LABELS)
    if sorted(active) != list(active) and labels is not None:
        # A caller-specified order is respected, but recorded, because it decides
        # the derived sub-seeds and therefore the split itself.
        pass
    train_rows, eval_rows = split_rows(
        rows, seed=seed, train_fraction=train_fraction, labels=active
    )

    out = resolve(out_dir)
    stem = prefix or ("toy" if labels is None else "toy_" + "_".join(active))
    train_path = out / f"{stem}_train.jsonl"
    eval_path = out / f"{stem}_eval.jsonl"
    _write_jsonl(train_path, train_rows)
    _write_jsonl(eval_path, eval_rows)

    manifest = SplitManifest(
        source=dataset_ref(source, name="toy_source", rows=rows),
        train=dataset_ref(train_path, name="train", rows=train_rows),
        eval=dataset_ref(eval_path, name="eval", rows=eval_rows),
        seed=seed,
        train_fraction=train_fraction,
        labels=active,
        prefix=stem,
    )

    manifest_path = out / f"{stem}.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.as_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_manifest(
    manifest_path: Path | str,
    *,
    train_path: Path | str,
    eval_path: Path | str,
) -> list[str]:
    """Return the ways the files on disk differ from the manifest (empty = clean)."""

    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    problems: list[str] = []
    for name, path in (("train", train_path), ("eval", eval_path)):
        expected = payload["splits"][name]
        ref = DatasetRef(
            name=name,
            path=expected["path"],
            sha256=sha256_file(path),
            rows=len(read_jsonl(path)),
        )
        try:
            check_expected(ref, sha256=expected["sha256"], rows=expected["rows"])
        except ValueError as exc:
            problems.append(str(exc))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.data.toy",
        description="Rebuild the deterministic toy splits from the committed fixture.",
    )
    parser.add_argument("--source", default=None, help="fixture path (default: ml/data/toy/)")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--train-fraction", type=float, default=0.7)
    parser.add_argument(
        "--labels",
        default=None,
        help="comma-separated subset of labels, e.g. `joy,anger` (default: all six)",
    )
    parser.add_argument("--prefix", default=None, help="output file stem (default: derived)")
    parser.add_argument(
        "--fingerprint",
        action="store_true",
        help="print only the sha256 of the source and of the manifest (for DVC/CI logs)",
    )
    args = parser.parse_args(argv)

    manifest = build_splits(
        source_path=args.source,
        out_dir=args.out_dir,
        seed=args.seed,
        train_fraction=args.train_fraction,
        labels=[item.strip() for item in args.labels.split(",")] if args.labels else None,
        prefix=args.prefix,
    )

    if args.fingerprint:
        manifest_path = resolve(args.out_dir) / f"{manifest.prefix}.manifest.json"
        print(f"source_sha256={manifest.source.sha256}")
        print(f"manifest_sha256={sha256_text(manifest_path.read_text(encoding='utf-8'))}")
        return 0

    print(
        f"toy splits written: train={manifest.train.rows} rows "
        f"sha256={manifest.train.sha256[:12]}… eval={manifest.eval.rows} rows "
        f"sha256={manifest.eval.sha256[:12]}… (seed={manifest.seed})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
