"""GoEmotions (English) → Manovia's nine emotions: download once, convert locally.

Licence: Apache-2.0 (Google Research), verified 2026-10-13 — see
``ml/data/CATALOGUE.md``. The *text* is Reddit comments, so the catalogue marks
it "use with conditions": never redistribute the rows in this repository, only
the hash (DVC keeps the bytes out of Git).

Two honest limitations, both of which the notebook prints rather than hides:

* **Multi-label → single-label.** GoEmotions rows carry up to five emotions.
  Manovia's analyser emits one, so the conversion keeps the first label in
  ``PRIORITY``. That is a modelling choice, and it throws away information: a
  row labelled ["sadness", "grief"] becomes sadness and grief disappears into it.
* **Domain shift.** Reddit comments are not what a person types into a
  wellbeing companion, and the mapping is not one-to-one: GoEmotions has no
  "loneliness" class at all, so nothing here can teach that label. The catalogue
  and the model card both say so.

The conversion itself is pure and tested offline (``ml/tests/test_goemotions.py``);
only ``--download`` touches the network, and it is never run in CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "GOEMOTIONS_LABELS",
    "LABEL_MAP",
    "PRIORITY",
    "conversion_report",
    "convert",
    "main",
]

#: The 28 GoEmotions classes, in the order the published dataset indexes them.
GOEMOTIONS_LABELS: tuple[str, ...] = (
    "admiration",
    "amusement",
    "anger",
    "annoyance",
    "approval",
    "caring",
    "confusion",
    "curiosity",
    "desire",
    "disappointment",
    "disapproval",
    "disgust",
    "embarrassment",
    "excitement",
    "fear",
    "gratitude",
    "grief",
    "joy",
    "love",
    "nervousness",
    "optimism",
    "pride",
    "realization",
    "relief",
    "remorse",
    "sadness",
    "surprise",
    "neutral",
)

#: GoEmotions class → Manovia emotion. Unmapped classes are dropped, not guessed.
LABEL_MAP: dict[str, str] = {
    "joy": "joy",
    "amusement": "joy",
    "excitement": "joy",
    "gratitude": "joy",
    "love": "joy",
    "optimism": "joy",
    "pride": "joy",
    "relief": "calm",
    "caring": "joy",
    "admiration": "joy",
    "approval": "joy",
    "desire": "joy",
    "sadness": "sadness",
    "grief": "sadness",
    "disappointment": "sadness",
    "remorse": "shame",
    "embarrassment": "shame",
    "anger": "anger",
    "annoyance": "anger",
    "disapproval": "anger",
    "disgust": "anger",
    "fear": "fear",
    # Manovia separates fear from anxiety; GoEmotions calls the second one
    # nervousness, and conflating them would erase the distinction the
    # analyser's UX depends on (a worry vs a dread).
    "nervousness": "anxiety",
    "confusion": "anxiety",
    "neutral": "neutral",
}

#: Which emotion wins when a row is multi-label. Ordered from most to least
#: specific: an explicit "sadness" beats a co-occurring "neutral", and a
#: "nervousness" is preferred over the broader "confusion".
PRIORITY: tuple[str, ...] = (
    "sadness",
    "shame",
    "fear",
    "anxiety",
    "anger",
    "joy",
    "calm",
    "neutral",
)


def _labels_of(record: dict[str, Any]) -> list[str]:
    raw = record.get("labels", record.get("label"))
    if raw is None:
        raise ValueError(f"record {record.get('id', '<no id>')}: no labels field")
    if isinstance(raw, int):
        return [GOEMOTIONS_LABELS[raw]]
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [
            GOEMOTIONS_LABELS[item] if isinstance(item, int) else str(item) for item in raw
        ]
    raise ValueError(f"record {record.get('id', '<no id>')}: unsupported labels {type(raw)}")


def _mapped(labels: list[str], *, keep_unmapped: bool) -> str | None:
    mapped = {LABEL_MAP[label] for label in labels if label in LABEL_MAP}
    if not mapped:
        if keep_unmapped:
            return "unmapped"
        return None
    for emotion in PRIORITY:
        if emotion in mapped:
            return emotion
    return sorted(mapped)[0]


def _row_id(record: dict[str, Any], index: int, split: str) -> str:
    if "id" in record:
        return f"goemotions-{split}-{record['id']}"
    digest = hashlib.sha256(str(record.get("text", "")).encode("utf-8")).hexdigest()[:12]
    return f"goemotions-{split}-{index}-{digest}"


def convert(
    records: Iterable[dict[str, Any]],
    *,
    split: str = "train",
    keep_unmapped: bool = False,
    drop_neutral: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield Manovia-shaped rows: ``id, text, lang, label, source, source_labels``."""

    for index, record in enumerate(records):
        text = str(record.get("text", "")).strip()
        if not text:
            continue
        source_labels = _labels_of(record)
        label = _mapped(source_labels, keep_unmapped=keep_unmapped)
        if label is None or (drop_neutral and label == "neutral"):
            continue
        yield {
            "id": _row_id(record, index, split),
            "text": text,
            "lang": "en",
            "label": label,
            "source": "goemotions",
            "source_labels": sorted(source_labels),
        }


def conversion_report(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Counts a human needs to judge the conversion before training on it."""

    by_label = Counter(str(row["label"]) for row in rows)
    unmapped = sum(1 for row in rows if row["label"] == "unmapped")
    return {
        "rows": len(rows),
        "by_label": dict(sorted(by_label.items())),
        "unmapped": unmapped,
        "languages": dict(sorted(Counter(str(row["lang"]) for row in rows).items())),
    }


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]], stats: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    (path.parent / f"{path.stem}.stats.json").write_text(
        json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _download(split: str) -> Iterable[dict[str, Any]]:
    """Fetch the raw split from Hugging Face. Network; never called by tests/CI."""

    try:
        from datasets import load_dataset  # noqa: PLC0415 - optional, network-only path
    except ImportError as exc:  # pragma: no cover - Colab/Kaggle only
        raise SystemExit(
            "the `datasets` package is not installed; on Colab run: pip install datasets"
        ) from exc
    dataset = load_dataset("google-research-datasets/go_emotions", "raw", split=split)
    return (dict(row) for row in dataset)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.data.goemotions",
        description="Convert GoEmotions JSONL (or a Hugging Face split) into Manovia's schema.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="local JSONL of GoEmotions raw records")
    source.add_argument(
        "--download",
        metavar="SPLIT",
        help="download a split from Hugging Face (train/validation/test); needs `datasets`",
    )
    parser.add_argument("--out", required=True, help="destination JSONL")
    parser.add_argument("--split-name", default="train", help="used for ids and the stats file")
    parser.add_argument("--keep-unmapped", action="store_true")
    parser.add_argument("--drop-neutral", action="store_true")
    args = parser.parse_args(argv)

    if args.input:
        with Path(args.input).open("r", encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
    else:
        records = list(_download(args.download))

    rows = list(
        convert(
            records,
            split=args.split_name,
            keep_unmapped=args.keep_unmapped,
            drop_neutral=args.drop_neutral,
        )
    )
    stats = conversion_report(rows)
    _write_jsonl(Path(args.out), rows, stats)
    print(json.dumps(stats, indent=2, sort_keys=True))
    print(f"wrote {len(rows)} rows to {args.out} (stats beside it)")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
