# Notebooks

Exploration only (AGENTS.md, ML ground rule 7). Anything reusable moves into
`ml/src/manovia_ml/` with a test; a notebook may not be the only home of a
decision, and no notebook result is quoted in a report or a PR without a run id
from a run of the scripts in `ml/src/`.

| notebook | runs where | data | status |
| --- | --- | --- | --- |
| [`colab_goemotions_tfidf.ipynb`](colab_goemotions_tfidf.ipynb) | Google Colab or Kaggle (free CPU is enough) | GoEmotions (Apache-2.0, see the catalogue § 1) | not run in this sandbox — no GPU and a network download; it is the ready-to-run path for the first real training run |
| [`colab_emotion_lora.ipynb`](colab_emotion_lora.ipynb) | Google Colab or Kaggle (a free T4 is enough; CPU works but is slow) | GoEmotions converted to Manovia's nine emotions + the committed 174-row hi/bn/en set | not run in this sandbox — no GPU, and the hub is unreachable; the same code path ran on CPU against a tiny random-init model (`configs/emotion_lora_smoke.yaml`), so what is untested here is the data and the scale, not the loop |
| [`colab_safety_lora.ipynb`](colab_safety_lora.ipynb) | Google Colab or Kaggle (a free T4 is enough; CPU works but is slow) | the synthetic safety corpus expanded from the Day-9 train/dev seeds (`dvc repro safety_expand`), evaluated on the frozen Day-9 test split | not run in this sandbox — no GPU, and the hub is unreachable; the same code path ran on CPU against a tiny random-init model (`configs/safety_lora_smoke.yaml`, `make safety-smoke`), so what is untested here is the data and the scale, not the loop |

## Why the notebook exists at all

The Day-31 sandbox has no GPU and no permission to download a corpus, and a
training notebook is the one deliverable that *needs* both. So the notebook is
thin on purpose: it does not reimplement anything. It installs the repository,
runs `manovia_ml.data.goemotions` to download and convert GoEmotions, runs
`manovia_ml.training.train` with `configs/colab_goemotions_logreg.yaml`, and
prints the run id — the same code path, on data this sandbox cannot reach.

That is the property that makes it trustworthy: if the notebook and the smoke run
disagree, they disagree about *data*, not about training logic.

## The LoRA notebook (Day 32)

`colab_emotion_lora.ipynb` fine-tunes `FacebookAI/xlm-roberta-base` (MIT; the
MuRIL alternative and why XLM-R is the default are in
[ADR 0032](../../docs/adr/0032-emotion-lora-finetune.md)) with LoRA into the nine
emotions, tunes per-label thresholds on the *validation* split, evaluates on the
hand-written en/hi/bn set that is never trained on, and packages the artefact.

It is deliberately thin in the same way: it calls
`manovia_ml.training.emotion.data convert`,
`manovia_ml.training.emotion.train`, `.evaluate` and `.tune` — the modules the
test suite covers — rather than reimplementing anything. The one piece of glue
that only exists in the notebook is pinning the fresh corpus hashes into a
run-local **copy** of the config; the committed config stays a template with
`sha256: null` until the owner pastes the measured numbers in.

```bash
cd ml
python -m manovia_ml.training.emotion.train   --config configs/emotion_lora_xlmr.yaml
python -m manovia_ml.training.emotion.evaluate --artifact artifacts/emotion/manovia-emotion-xlmr-v1 \
    --data data/validation/emotion_validation.jsonl --bootstrap 2000 --json
python -m manovia_ml.training.emotion.tune    --config configs/emotion_lora_xlmr.yaml --trials 12   # optuna.enabled first
```

## The safety LoRA notebook (Day 33)

`colab_safety_lora.ipynb` fine-tunes `FacebookAI/xlm-roberta-base` (MIT) with LoRA
into the five risk levels, using the cost-weighted loss from
`training/safety/taxonomy.py`, then calibrates with temperature scaling on a
dedicated calibration split and attaches a conformal prediction set at 95% target
coverage. The point prediction is the expected-cost argmin; the abstention policy
is the MEDIUM floor.

It is generated from `notebooks/_build_safety_notebook.py` rather than hand-edited,
so the cell list is reviewable as code — regenerate it with:

```bash
cd ml && python notebooks/_build_safety_notebook.py
```

```bash
cd ml
python -m manovia_ml.training.safety.train    --config configs/safety_lora_xlmr.yaml
python -m manovia_ml.training.safety.evaluate --artifact artifacts/safety/manovia-safety-xlmr-v1 \
    --data ../evals/datasets/crisis_cases_test.jsonl --train data/safety/train.jsonl --bootstrap 2000 --json
python -m manovia_ml.training.safety.explain  --artifact artifacts/safety/manovia-safety-xlmr-v1 \
    --data ../evals/datasets/crisis_cases_test.jsonl --n 10 --json
```

Three things to read before quoting anything from that run: the artefact's
`MODEL_CARD.md`, the held-out conformal coverage against the 0.95 target, and the
`explain` output's shortcut signals. The corpus is synthetic and templated, so a
high number on it is a statement about this corpus, not about crisis language.

Run the smoke config first on any new machine: it is the same pipeline in
seconds, and it is what tells you whether a failure is your environment or the
real run (`docs/adr/0032-emotion-lora-finetune.md` § verification).

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
