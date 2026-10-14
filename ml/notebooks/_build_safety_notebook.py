"""Build ml/notebooks/colab_safety_lora.ipynb from a single source of truth.

The notebook is generated rather than hand-edited so the cell list is reviewable
as code, and so a change to the CLI (a renamed flag, a new stage) is a one-line
edit here instead of a JSON diff. Mirrors the structure of
``colab_emotion_lora.ipynb``: accelerator check, install, smoke, data, pin, train,
evaluate in a fresh process, explain, then what to bring back.
"""

from __future__ import annotations

import json
from pathlib import Path

NOTEBOOK = Path(__file__).resolve().parent

CELLS: list[tuple[str, str]] = []


def md(text: str) -> None:
    CELLS.append(("markdown", text.strip("\n")))


def code(text: str) -> None:
    CELLS.append(("code", text.strip("\n")))


md(
    """
# Fine-tune the safety classifier (cost-sensitive LoRA) — Colab / Kaggle

**Status: not executed in the Manovia sandbox.** There is no GPU there and the
Hugging Face hub is blocked, so this notebook is the ready-to-run path for the
Day-33 real run. What *has* run offline is the same pipeline against a
random-init tiny XLM-R (`configs/safety_lora_smoke.yaml`, `make safety-smoke`),
so the loop, the calibration fit, the conformal quantile, the artefact layout and
the MLflow record are all covered by tests. What is untested here is the data and
the scale.

**This model is not a safety decision.** It is a backstop that can only *raise*
caution, and only through the reviewed ensemble in
`backend/app/services/safety/ensemble.py`. Nothing in this notebook changes that.

The notebook is thin on purpose: it installs the repository, regenerates the
synthetic corpus, pins its hashes into a run-local copy of the config, trains,
evaluates in a fresh process, and prints token attributions for flagged
examples. It reimplements nothing.
"""
)

md(
    """
## 1. Is there an accelerator?

The run is fine-tuned with fp16 on a T4/V100 (Colab) or P100 (Kaggle). On a
CPU-only VM it still works — `mixed_precision: auto` resolves to fp32 — but
`xlm-roberta-base` over ~3,450 rows takes hours per epoch, so a free CPU session
will time out. That is a scheduling problem, not a correctness one.
"""
)

code(
    """
import subprocess

print(
    subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout
    or "no nvidia-smi: CPU only"
)
try:
    import torch

    print("torch", torch.__version__, "cuda", torch.cuda.is_available())
except ImportError:  # pragma: no cover - the install cell has not run yet
    print("torch not installed yet")
"""
)

md(
    """
## 2. Install

Colab/Kaggle already ship a torch build; `ml[ml,dev]` pulls the data and tracking
stack (`numpy`, `scikit-learn`, `pandas`, `mlflow`, `dvc`). The training stack is
pinned explicitly so the versions match the ones the test suite runs against.
"""
)

code(
    """
!git clone --depth 1 https://github.com/BuildbyArindam/Manovia.git
%cd Manovia
!pip install -q -e "./ml[ml,dev]"
!pip install -q "transformers>=4.44" "peft>=0.11" optuna accelerate datasets
!python -c "import torch, transformers, peft, mlflow; print('stack ok')"
"""
)

md(
    """
## 3. Sanity check first: the tiny smoke run

This trains a 2-layer random-init XLM-R for 12 steps on the committed toy
fixture, and prints the calibration temperature, the conformal quantile, the
abstention rate and the TF-IDF comparison. It proves *this VM* can run the whole
pipeline — data → cost-weighted LoRA → temperature scaling → conformal sets →
abstention → artefact → MLflow — before you spend an hour discovering it cannot.

Its numbers are noise by construction (random weights, 20 rows per split) and
every report it writes says so. If a number below looks good, that is the
warning, not the result.
"""
)

code(
    """
!cd ml && python -m manovia_ml.training.safety.train \\
    --config configs/safety_lora_smoke.yaml --phase smoke --json | tail -50
"""
)

md(
    """
## 4. Data: the synthetic safety corpus, regenerated from the Day-9 seeds

No network, no download, and **no real people's messages** — by rule. The builder
expands the hand-written Day-9 train/dev seeds with a template paraphraser and
adversarial augmentation (typos, spacing, romanised Hindi, code-mixing, emoji),
then filters every row through the rules engine for label consistency and drops
the ones it contradicts. Provenance travels on each row, so any row can be traced
back to its seed.

The catalogue entry is `ml/data/CATALOGUE.md` § 15. The frozen Day-9 **test**
split is never read here: it is an input to the evaluator only.
"""
)

code(
    """
!cd ml && python -m dvc repro safety_expand
"""
)

code(
    """
# What came out, and what was dropped. The drop reasons are the audit trail:
# a corpus with no drops usually means the filter is not filtering.
import json
import pathlib

manifest = json.loads(pathlib.Path("ml/data/safety/manifest.json").read_text(encoding="utf-8"))
print(json.dumps(manifest, indent=2, ensure_ascii=False)[:2000])
dropped = pathlib.Path("ml/data/safety/dropped.jsonl")
if dropped.is_file():
    rows = [
        json.loads(line)
        for line in dropped.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    from collections import Counter

    print("dropped:", len(rows), Counter(row.get("drop_reason", "?") for row in rows))
"""
)

md(
    """
## 5. Version the bytes (never commit them)

DVC records the hashes; the rows stay on the VM. `dvc add` writes a `.dvc` file
that names the bytes without containing them, which is what lets a 3,445-row
corpus be checkable without committing it.
"""
)

code(
    """
!cd ml && python -m dvc add data/safety
!cd ml && dvc status
"""
)

md(
    """
## 6. Pin the hashes into a run-local copy of the config

`configs/safety_lora_xlmr.yaml` ships with `sha256: null` on the generated splits
on purpose: a config that pins a number nobody measured is worse than one that
refuses to run. This cell pastes the measured numbers into a **copy**, so the
committed config stays a template.

The test split's hash is *not* rewritten here. It is pinned in
`evals/datasets/manifest.json`, and the trainer refuses to run if a training row
appears verbatim in it — so a copy of the frozen hash is the whole point.
"""
)

code(
    """
import re
import shutil

from manovia_ml.hashing import sha256_file

template = pathlib.Path("ml/configs/safety_lora_xlmr.yaml")
local = pathlib.Path("ml/configs/_colab_safety_lora_xlmr.yaml")
shutil.copyfile(template, local)
text = local.read_text(encoding="utf-8")

for split, path in (
    ("train", "ml/data/safety/train.jsonl"),
    ("dev", "ml/data/safety/dev.jsonl"),
    ("calibration", "ml/data/safety/calibration.jsonl"),
):
    digest = sha256_file(path)
    rows = sum(1 for line in open(path, encoding="utf-8") if line.strip())
    # Only replace the null pin inside this split's block.
    block = re.search(
        rf"(  {split}:\\n(?:    .*\\n)+?)(    sha256: null\\n    rows: null\\n)",
        text,
    )
    assert block, f"could not find the {split} block to pin"
    text = (
        text[: block.start(2)]
        + f'    sha256: "{digest}"\\n    rows: {rows}\\n'
        + text[block.end(2) :]
    )

local.write_text(text, encoding="utf-8")
print("pinned into", local)
print(text[:1200])
"""
)

md(
    """
## 7. Train

`mixed_precision: auto` → fp16 on CUDA. The loss is the cost-weighted cross
entropy from `training/safety/taxonomy.py`: a missed IMMINENT costs 25, the
cheapest off-diagonal cell is 1. The point prediction is the expected-cost
argmin, not the argmax.

Model selection is **crisis recall on dev** (macro-F1 breaks ties), because a
cost-sensitive model that trades away the number it was built for has failed at
the thing it was built for. The temperature and the conformal quantile are fitted
on the *calibration* split and nowhere else.
"""
)

code(
    """
!cd ml && python -m manovia_ml.training.safety.train \\
    --config configs/_colab_safety_lora_xlmr.yaml --json | tail -60
"""
)

md(
    """
## 8. Read the card before quoting any number

`MODEL_CARD.md` is written into the artefact by the trainer. It is where the
honesty lives: which splits were fitted on, what the cost matrix was, what the
abstention policy does, and what the corpus cannot represent. A number quoted
without the card next to it is a number nobody can check.
"""
)

code(
    """
import pathlib

artifact = pathlib.Path("ml/artifacts/safety/manovia-safety-xlmr-v1")
card = artifact / "MODEL_CARD.md"
print(card.read_text(encoding="utf-8") if card.is_file() else "no card: the run failed")
"""
)

md(
    """
## 9. Evaluate in a fresh process, from the artefact alone

`evaluate.py` refuses to run if the training module was imported, so the numbers
below cannot come from anything but the files on disk. This is the check that
matters for trust, and it is the same one the test suite runs against the smoke
artefact.

Watch three things in the output: the **held-out conformal coverage** against the
0.95 target, the **abstention rate**, and the **leakage** line (`max_jaccard`,
`exact_overlap`). `exact_overlap` must be 0; the trainer now refuses to run if it
is not.
"""
)

code(
    """
!cd ml && python -m manovia_ml.training.safety.evaluate \\
    --artifact artifacts/safety/manovia-safety-xlmr-v1 \\
    --data ../evals/datasets/crisis_cases_test.jsonl \\
    --train data/safety/train.jsonl \\
    --bootstrap 2000 --json | tail -60
"""
)

md(
    """
## 10. Attribution review: catch the shortcut before a human does

Integrated Gradients over the flagged examples, plus the three shortcut signals
(single-token dominance, off-lexicon top token, copy-paste attributions). This is
a **developer tool** — the output must never be shown to a user.

The question it exists to answer: has the model learned *crisis language*, or has
it learned one word that happens to appear in the training rows? A model at 1.000
recall that keys on a single token is worse than a weaker model, because its
number cannot be trusted on text it has not seen.
"""
)

code(
    """
!cd ml && python -m manovia_ml.training.safety.explain \\
    --artifact artifacts/safety/manovia-safety-xlmr-v1 \\
    --data ../evals/datasets/crisis_cases_test.jsonl \\
    --n 10 --json 2>&1 | tail -60
"""
)

md(
    """
## 11. Side-by-side against the Day-9 baseline, through the real backend

This is the comparison Message 2 asks for, run through the production selector
rather than a private code path, so a fallback is visible instead of silent. With
no artefact deployed, `--model trained` reports the baseline and says so.
"""
)

code(
    """
!cd /content/Manovia && python evals/run_safety_eval.py --split test --model trained --no-gate | tail -20
"""
)

md(
    """
## 12. What to bring back into the repository

Copy these into the repo (they are small; the weights and the rows are **not**):

| bring back | why |
| --- | --- |
| the two `sha256`/`rows` pins from step 6 | so the committed config stops being a template |
| the evaluate JSON from step 9 | the held-out coverage, abstention rate and leakage numbers |
| the `explain` JSON from step 10 | the attribution evidence, ids only, never the texts |
| the report and the card from the artefact | the honest write-up, with the limits in it |
| `ml/mlruns` (the metrics, config and environment) | so the run is reproducible |

Do **not** bring back: model weights (`.safetensors`/`adapter_model.bin`), the
corpus rows, or `ml/artifacts/`. The `.gitignore` already excludes them; a large
binary pasted into a PR is the one mistake this repo cannot undo.

## Known limits of this run, stated before it happens

* ~3,450 templated synthetic rows. The corpus is far less lexically diverse than
  its row count suggests, and a number measured on it is a number about it.
* One writer's English, Hindi and Bengali. No native-speaker review has happened.
* The IMMINENT tier is the smallest and the most consequential; on the Day-9
  test split the rules-only baseline already misses most of it. Watch that row
  first.
* 95% conformal coverage is a guarantee about the *calibration* split's exchange
  with the test split. If the test split is not exchangeable with it — and with a
  synthetic corpus it may not be — the coverage number is optimistic.
"""
)


def main() -> int:
    cells = []
    for cell_type, source in CELLS:
        cells.append(
            {
                "cell_type": cell_type,
                "metadata": {},
                "source": source.splitlines(keepends=True),
            }
        )
        if cell_type == "code":
            cells[-1]["outputs"] = []
            cells[-1]["execution_count"] = None

    notebook = {
        "cells": cells,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"provenance": [], "toc_visible": True},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    target = NOTEBOOK / "colab_safety_lora.ipynb"
    target.write_text(json.dumps(notebook, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {target} ({len(cells)} cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
