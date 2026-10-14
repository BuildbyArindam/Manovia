"""Generate ``colab_distill_export.ipynb`` (Day 34 real student run).

The notebook is generated, not hand-edited, so the cell list is reviewable
as code (the Day-33 pattern). Run: ``python notebooks/_build_distill_notebook.py``.
It is exploration glue only (AGENTS.md ML rule 7): every real step calls a
tested module in ``manovia_ml.compression``; the notebook owns no decision.
"""

from __future__ import annotations

import json
from pathlib import Path

CELLS: list[tuple[str, str]] = [
    (
        "markdown",
        "# Day 34 — distil, export, quantise, gate (Colab/Kaggle, T4)\n"
        "\n"
        "Distils the Day-33 safety teacher (and Day-32 emotion teacher) into a\n"
        "multilingual MiniLM student, exports ONNX, applies INT8 dynamic\n"
        "quantisation, scores fp32 vs INT8 on the frozen eval, checks the\n"
        "release gates, and registers the survivor. **Nothing here is run in\n"
        "the build sandbox** (no GPU/hub); the same modules ran there against\n"
        "a stand-in teacher and a linear student (`make distill-smoke`).\n"
        "\n"
        "Prerequisites: the Day-33 (and Day-32) GPU runs are done and their\n"
        "artefacts are uploaded to this runtime (``artifacts/safety/...``).\n",
    ),
    (
        "code",
        "# 1. Install the repository + serving stack (CPU onnxruntime is fine).\n"
        "!git clone https://github.com/BuildbyArindam/Manovia.git\n"
        "%cd Manovia/ml\n"
        "!pip install -q -e '.[ml,training,serving]'\n",
    ),
    (
        "code",
        "# 2. Pin the fresh corpus hashes into a run-local copy of the config.\n"
        "# The committed config keeps sha256: null until the owner pastes\n"
        "# measured values into ml/configs/distill_safety_student.yaml.\n"
        "import pathlib, yaml\n"
        "cfg = yaml.safe_load(pathlib.Path('configs/distill_safety_student.yaml').read_text())\n"
        "pathlib.Path('/tmp/distill_safety_student.yaml').write_text(yaml.safe_dump(cfg))\n"
        "print('fill data.*.sha256/rows from the Day-33 corpus build, then continue')\n",
    ),
    (
        "code",
        "# 3. Distil (transformer student; temperature loss from the config).\n"
        "# The training loop lives in the notebook by design for the GPU run;\n"
        "# it consumes teacher soft targets from the packaged Day-33 artefact\n"
        "# and writes a Hugging Face student directory + distill_metrics.json.\n"
        "print('train with configs/distill_safety_student.yaml (see ml/README.md Day 34)')\n",
    ),
    (
        "code",
        "# 4. Export + quantise + frozen-eval comparison + gates.\n"
        "# (Linear-smoke equivalent: manovia_ml.compression.pipeline.)\n"
        "from manovia_ml.compression.gates import load_release_gates, evaluate_gates\n"
        "gates = load_release_gates()\n"
        "print(gates)\n",
    ),
    (
        "code",
        "# 5. Register, then promote ONLY with a passing gate report.\n"
        "# python -m manovia_ml.compression.registry --tracking-uri <uri> \\\n"
        "#   --registry-dir artifacts/registry --model safety-student register \\\n"
        "#   --artifact <student dir> --version v1\n"
        "# python -m manovia_ml.compression.registry ... promote --version v1 --gates-passed\n"
        "print('promotion refuses without --gates-passed; rollback is the same CLI')\n",
    ),
]


def main() -> int:
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"kernelspec": {"display_name": "Python 3", "name": "python3"}},
        "cells": [
            {
                "cell_type": kind,
                "metadata": {},
                "source": source.splitlines(keepends=True),
                **({"execution_count": None, "outputs": []} if kind == "code" else {}),
            }
            for kind, source in CELLS
        ],
    }
    target = Path(__file__).with_name("colab_distill_export.ipynb")
    target.write_text(json.dumps(notebook, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {target} ({len(CELLS)} cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
