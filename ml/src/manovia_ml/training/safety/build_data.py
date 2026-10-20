"""Build the safety corpus: expand the seeds, filter, split, write the manifest.

One command, run by the DVC stage ``safety_expand`` in ``ml/dvc.yaml``::

    python -m manovia_ml.training.safety.build_data \
        --seeds ../../evals/datasets/crisis_cases_train.jsonl \
        --seeds ../../evals/datasets/crisis_cases_dev.jsonl \
        --out-dir data/safety --seed 20261013

What it writes (never committed — see ``ml/.gitignore``; DVC records the hashes):

| file | what it is |
| --- | --- |
| `train.jsonl` | the expanded, filtered training rows |
| `dev.jsonl` | model selection (early stopping, level choice) |
| `calibration.jsonl` | temperature scaling and the conformal quantile |
| `dropped.jsonl` | every dropped row with its reason, for review |
| `manifest.json` | hashes, counts, seeds, generator version, drop statistics |

The **frozen test split is not an input.** It lives in ``evals/datasets/`` and is
read by the evaluator alone. That is not a convention this module could break by
accident: there is no code path here that opens it.

``--toy`` switches to the hand-written fixture under ``ml/data/toy/`` and writes
the four-way split the smoke config needs. Same code, 80 rows instead of 3,400.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from manovia_ml.hashing import sha256_file
from manovia_ml.paths import ml_root, resolve
from manovia_ml.training.safety.augment import ExpansionConfig, expand
from manovia_ml.training.safety.data import (
    assign_splits,
    backend_rules_filter,
    dataset_ref,
    load_examples,
    load_seed_split,
    rows_to_examples,
    summary,
    write_examples,
    write_manifest,
)
from manovia_ml.training.safety.providers import build_paraphraser
from manovia_ml.training.safety.taxonomy import LEVELS

__all__ = ["build", "main"]

#: The paraphraser version recorded in the manifest. Bump it when the rule tables
#: change, because a corpus built by ``fake-v1`` and one built by ``fake-v2`` are
#: different corpora even if the row counts match.
GENERATOR_VERSION = "fake-v1"


def _display_path(path: str | Path) -> str:
    """The path a manifest records: relative to ``ml/``, never absolute.

    A manifest is a DVC output, so its bytes are what ``dvc.lock`` pins. An
    absolute path made the hash depend on the checkout directory, which meant
    two machines building identical rows disagreed about the lock — found on
    Day 49 by running ``dvc repro`` on a fresh clone. Paths inside ``ml/`` are
    recorded relative to it; the evaluation corpora live one level up in
    ``evals/`` and are recorded as ``../evals/...``; anything else keeps its
    resolved form rather than pretending to be relative.
    """

    resolved = Path(path).resolve()
    for base, prefix in ((ml_root(), ""), (ml_root().parent, "../")):
        try:
            return prefix + resolved.relative_to(base).as_posix()
        except ValueError:
            continue
    return resolved.as_posix()


def build(
    *,
    seed_paths: Sequence[str | Path],
    out_dir: str | Path,
    seed: int = 20261013,
    paraphrases_per_seed: int = 4,
    adversarial_per_row: int = 1,
    provider: str = "fake",
    max_rows: int = 20_000,
    rules_filter: bool = True,
) -> dict[str, Any]:
    """Expand, filter and split; write the corpus and return the manifest."""
    seeds: list[dict[str, Any]] = []
    for path in seed_paths:
        seeds.extend(load_seed_split(path))
    if not seeds:
        raise SystemExit("no seed rows: nothing to expand")

    assessor = backend_rules_filter() if rules_filter else None
    paraphraser = build_paraphraser(provider)
    config = ExpansionConfig(
        paraphrases_per_seed=paraphrases_per_seed,
        adversarial_per_row=adversarial_per_row,
        max_rows=max_rows,
        seed=seed,
    )
    result = expand(
        seeds,
        paraphraser=paraphraser,
        config=config,
        rules_assessor=assessor,
        generator_name=f"{GENERATOR_VERSION}:{paraphraser.name}",
    )
    examples = rows_to_examples(result.rows)
    splits = assign_splits(examples, seed=seed)

    target = resolve(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = {name: write_examples(target / f"{name}.jsonl", rows) for name, rows in splits.items()}

    dropped_path = target / "dropped.jsonl"
    with dropped_path.open("w", encoding="utf-8") as handle:
        for row_id, reason in result.dropped:
            handle.write(json.dumps({"id": row_id, "reason": reason}) + "\n")

    refs = {name: dataset_ref(name, path) for name, path in paths.items()}
    manifest = {
        # No "built" timestamp on purpose: this file is a DVC output, so a date
        # in it rewrote the lock every single day and `dvc status` could never be
        # clean. Provenance is the seed hashes, the generator version and the
        # MLflow run, none of which move without the data moving. When the build
        # happened is in the CI log and in `mlruns/`.
        "generator": f"{GENERATOR_VERSION}:{paraphraser.name}",
        "provider": provider,
        "seed": seed,
        "expansion": {
            "paraphrases_per_seed": paraphrases_per_seed,
            "adversarial_per_row": adversarial_per_row,
            "max_rows": max_rows,
        },
        "rules_filter": "backend" if assessor is not None else "unavailable",
        "seed_files": [
            {
                "path": _display_path(path),
                "sha256": sha256_file(Path(path)),
                "rows": len(load_seed_split(path)),
            }
            for path in seed_paths
        ],
        "stats": result.stats,
        "drop_reasons": dict(sorted(result.drop_reasons.items())),
        "summaries": {name: summary(rows) for name, rows in splits.items()},
        "splits": {name: ref.as_dict() for name, ref in refs.items()},
        "labels": list(LEVELS),
        "test_split": "evals/datasets/crisis_cases_test.jsonl (frozen; read by the evaluator only)",
    }
    write_manifest(target / "manifest.json", manifest)
    return manifest


def _build_toy(*, out_dir: str | Path, seed: int, test_share: float = 0.15) -> dict[str, Any]:
    """The smoke-run split of the hand-written fixture under ``ml/data/toy/``.

    The only place a fourth slice is cut: the real corpus's held-out split is the
    frozen Day-9 file, not a splitter's output.
    """
    fixture = resolve("data/toy/safety_toy.jsonl")
    examples = load_examples(fixture)
    splits = assign_splits(
        examples,
        seed=seed,
        train_share=0.65,
        dev_share=0.15,
        test_share=test_share,
    )
    target = resolve(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = {name: write_examples(target / f"{name}.jsonl", rows) for name, rows in splits.items()}
    refs = {name: dataset_ref(name, path) for name, path in paths.items()}
    manifest = {
        # See the note in `build()`: no wall-clock date in a hashed artefact.
        "generator": "hand-written fixture (ml/data/toy/safety_toy.jsonl)",
        "seed": seed,
        "expansion": {"paraphrases_per_seed": 0, "adversarial_per_row": 0},
        "rules_filter": "not applied (fixture is hand-labelled)",
        "seed_files": [
            {
                "path": _display_path(fixture),
                "sha256": sha256_file(fixture),
                "rows": len(examples),
            }
        ],
        "stats": {"seeds": len(examples), "kept": len(examples), "dropped": 0, "drop_reasons": {}},
        "drop_reasons": {},
        "summaries": {name: summary(rows) for name, rows in splits.items()},
        "splits": {name: ref.as_dict() for name, ref in refs.items()},
        "labels": list(LEVELS),
        "test_split": "a slice of the fixture; the frozen Day-9 split is not used here",
    }
    write_manifest(target / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the synthetic safety corpus (Day 33). Never reads the frozen test split."
    )
    parser.add_argument(
        "--seeds",
        action="append",
        default=[],
        help="seed JSONL file; repeat the flag for several (train + dev seeds)",
    )
    parser.add_argument("--out-dir", default="data/safety")
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--paraphrases", type=int, default=4)
    parser.add_argument("--adversarial", type=int, default=1)
    parser.add_argument("--provider", default="fake", help="'fake' (offline) or 'llm'")
    parser.add_argument("--max-rows", type=int, default=20_000)
    parser.add_argument(
        "--no-rules-filter",
        action="store_true",
        help="skip the rules-engine label-consistency filter (reported, not hidden)",
    )
    parser.add_argument(
        "--toy",
        action="store_true",
        help="split the hand-written fixture under ml/data/toy/ instead of expanding seeds",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.toy:
        manifest = _build_toy(out_dir=args.out_dir, seed=args.seed)
    else:
        if not args.seeds:
            parser.error("--seeds is required unless --toy is given")
        manifest = build(
            seed_paths=args.seeds,
            out_dir=args.out_dir,
            seed=args.seed,
            paraphrases_per_seed=args.paraphrases,
            adversarial_per_row=args.adversarial,
            provider=args.provider,
            max_rows=args.max_rows,
            rules_filter=not args.no_rules_filter,
        )

    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        stats = manifest["stats"]
        print(f"generator={manifest['generator']}  rules_filter={manifest['rules_filter']}")
        print(
            f"seeds={stats.get('seeds', 0)} paraphrases={stats.get('paraphrases', 0)} "
            f"adversarial={stats.get('adversarial', 0)} kept={stats['kept']} "
            f"dropped={stats['dropped']}"
        )
        for name, entry in manifest["summaries"].items():
            print(
                f"  {name:<12} rows={entry['rows']:<6} labels={entry['labels']} "
                f"langs={entry['languages']}"
            )
        print(f"drop reasons: {manifest['drop_reasons']}")
        print(f"written to {resolve(args.out_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
