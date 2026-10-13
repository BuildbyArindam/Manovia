# ml/data

Data for the ML platform. The rule that shapes this directory: **no dataset
bytes in Git** (AGENTS.md, ML ground rule 3). Dataset files are versioned with
DVC; what is committed here is the reasoning around them.

| path | committed? | what it is |
| --- | --- | --- |
| [`CATALOGUE.md`](CATALOGUE.md) | yes | every dataset used, considered, planned or rejected, with licence, allowed use and the verdict. The summary table is generated (`make ml-catalogue`). |
| `toy/toy_emotion.jsonl` | yes | 84 hand-written sentences, 6 emotions × {en, hi, hi-Latn, bn}, authored in this repository for tests and the smoke run. Not a dataset in the catalogue sense — it is a fixture, and it contains no personal data. |
| `planned/hi_bn_emotion/` | yes | the draft dataset card for the hand-written Hindi/Bengali set (see the catalogue § "Hindi and Bengali emotion data"). Documentation only; no rows exist yet. |
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
