# Notebooks

Exploration only (AGENTS.md, ML ground rule 7). Anything reusable moves into
`ml/src/manovia_ml/` with a test; a notebook may not be the only home of a
decision, and no notebook result is quoted in a report or a PR without a run id
from a run of the scripts in `ml/src/`.

| notebook | runs where | data | status |
| --- | --- | --- | --- |
| [`colab_goemotions_tfidf.ipynb`](colab_goemotions_tfidf.ipynb) | Google Colab or Kaggle (free CPU is enough) | GoEmotions (Apache-2.0, see the catalogue § 1) | not run in this sandbox — no GPU and a network download; it is the ready-to-run path for the first real training run |

## Why the notebook exists at all

The Day-31 sandbox has no GPU and no permission to download a corpus, and a
training notebook is the one deliverable that *needs* both. So the notebook is
thin on purpose: it does not reimplement anything. It installs the repository,
runs `manovia_ml.data.goemotions` to download and convert GoEmotions, runs
`manovia_ml.training.train` with `configs/colab_goemotions_logreg.yaml`, and
prints the run id — the same code path, on data this sandbox cannot reach.

That is the property that makes it trustworthy: if the notebook and the smoke run
disagree, they disagree about *data*, not about training logic.

## Colab / Kaggle steps (exact)

```bash
# 1. Get the repository (any clone or upload works; this is the public one)
!git clone https://github.com/BuildbyArindam/Manovia.git
%cd Manovia

# 2. Install the backend (for the Python version/pins) and the ML stack
!pip install -q -e "./backend[dev]"
!pip install -q -e "./ml[ml,dev]"

# 3. Build the toy splits (so the smoke run at the end works offline)
!cd ml && python -m dvc repro

# 4. Download and convert GoEmotions (network; Apache-2.0 — see the catalogue)
!cd ml && pip install -q datasets
!cd ml && python -m manovia_ml.data.goemotions --download train --split-name train \
    --out data/raw/goemotions/train.jsonl
!cd ml && python -m manovia_ml.data.goemotions --download validation --split-name validation \
    --out data/raw/goemotions/validation.jsonl
!cd ml && python -m manovia_ml.data.goemotions --download test --split-name test \
    --out data/raw/goemotions/test.jsonl

# 5. Version the bytes with DVC (never commit them) and record the hashes
!cd ml && python -m dvc add data/raw/goemotions

# 6. Train. MLflow writes to ml/mlruns on the Colab VM: download it afterwards.
!cd ml && python -m manovia_ml.training.train --config configs/colab_goemotions_logreg.yaml

# 7. Evaluate the run it printed (re-scores the bundle, checks consistency)
!cd ml && python -m manovia_ml.evaluation.evaluate --run <RUN_ID>

# 8. Write the report, then bring these back into the repository:
#    the report (ml/reports/), the config's recorded baseline, and the run's
#    environment.json / data.json — never the model weights, never the rows.
!cd ml && python -m manovia_ml.reporting --run <RUN_ID>
```

Colab note: set `MLFLOW_TRACKING_URI` to an absolute `file:///content/Manovia/ml/mlruns`
if you change directory mid-notebook, and zip `ml/mlruns` before the VM is
recycled. The artefacts that matter for the repository are small (metrics,
config, environment); the model bundle is not committed (28 MB of `.joblib` is
already ignored, and it is a 2026 TF-IDF model, not an heirloom).
