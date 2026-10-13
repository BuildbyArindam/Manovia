# ml/data

Data for the ML platform. The rule that shapes this directory: **no dataset
bytes in Git** (AGENTS.md, ML ground rule 3). Dataset files are versioned with
DVC; what is committed here is the reasoning around them.

| path | committed? | what it is |
| --- | --- | --- |
| [`CATALOGUE.md`](CATALOGUE.md) | yes | every dataset used, considered, planned or rejected, with licence, allowed use and the verdict. The summary table is generated (`make ml-catalogue`). |
| `toy/toy_emotion.jsonl` | yes | 84 hand-written sentences, 6 emotions × {en, hi, hi-Latn, bn}, authored in this repository for tests and the smoke run. Not a dataset in the catalogue sense — it is a fixture, and it contains no personal data. |
| `planned/hi_bn_emotion/` | yes | the draft dataset card for the hand-written Hindi/Bengali set (see the catalogue § "Hindi and Bengali emotion data"). Documentation only; no rows exist yet. |
| `toy/safety_toy.jsonl` | yes | 80 hand-written synthetic sentences, 5 risk levels × {en, hi, hi-Latn, bn}, authored in this repository for the Day-33 smoke run and its tests. Contains no method words and no personal data, like the Day-9 eval set. | 
| `safety/`, `safety_toy/` | no (DVC out) | Day 33 outputs: the expanded synthetic corpus (2,000+ rows with provenance) and the fixture's derived splits. Rebuilt by `make ml-safety-data`; hashed by DVC and by the training run. |
| `raw/` | no (DVC) | downloaded corpora — GoEmotions, a future reviewed hi/bn set. Never in Git. |
| `processed/` | no (DVC out) | derived splits produced by `ml/dvc.yaml` (`make ml-data`). Regenerable, hashed, gitignored. |
| `downloads/` | no | scratch space for manual downloads; safe to delete. |

## Regenerating the splits

```bash
make ml-data      # dvc repro: 6-class splits + the joy/anger smoke slice
make ml-status    # dvc status: "Data and pipelines are up to date."
```

Both commands run DVC inside `ml/` with the project's interpreter. The splits are
a pure function of the fixture bytes, the seed (20261013) and the train fraction
(0.7); `dvc.lock` records the md5 of every output, and each training run records
the sha256 of the files it read, so a run can be tied to bytes from either side.

## The Day 33 safety corpus

`make ml-safety-data` runs `manovia_ml.training.safety.build_data`, which:

1. reads the hand-written Day-9 **train and dev** seeds from
   `evals/datasets/crisis_cases_{train,dev}.jsonl` (read-only inputs);
2. expands them with template + paraphrase generation (the offline
   `FakeParaphraser` by default; `--provider llm` uses the backend's LLM chain)
   and adversarial augmentation (typos, spacing, romanised Hindi/Bengali,
   code-mixing, emoji);
3. filters with the rules engine for label consistency, screens for method words
   and de-duplicates — every dropped row is written to `dropped.jsonl` with its
   reason;
4. writes `train.jsonl`, `dev.jsonl`, `calibration.jsonl` and a `manifest.json`
   with per-file sha256, row counts and drop statistics.

The **frozen test split is not an input** and there is no code path that opens
it: it is read by `manovia_ml.training.safety.evaluate` and by nothing else.

## Adding a real dataset

1. Read the licence **at the source** (not a mirror) and add a `catalogue:` block
   to `CATALOGUE.md` with `verified_on` set to today. Unclear or restrictive →
   `DO NOT USE`, and stop here.
2. Fetch it with a documented command (a script in `ml/src/manovia_ml/data/` with
   the network behind an explicit flag, e.g. `--download`). Never inside a DVC
   stage: `dvc repro` must stay hermetic and offline.
3. `dvc add data/raw/<name>` so the bytes are versioned, and commit the
   `.dvc` file — the hash, not the data.
4. Write the dataset card from `ml/templates/DATASET_CARD.md` and put it next to
   the data (or under `planned/` while the data does not exist yet).
5. Then, and only then, write a config that names the files.
