# Findings: what the toy slices actually measure (2026-10-13)

Recorded because these are the numbers every smoke run is compared against, and
because the first version of the config comments claimed a "real signal" that the
measurements do not support. Numbers below are from the committed configs at
seed `20261013`; reproduce with the commands at the end.

## Why the smoke config is a two-class slice

| | six-class (`toy_emotion_multiclass_logreg`) | binary (`toy_emotion_sgd`) |
| --- | --- | --- |
| split | 60 train / 24 eval rows | 20 train / 8 eval rows |
| classes | joy, sadness, anger, fear, calm, neutral | joy, anger |
| chance (uniform) | 0.167 | 0.500 |
| **majority-class floor** | 0.167 (4 rows of each of 24) | **0.500** (4 rows of each of 8) |
| accuracy | 0.208 | 0.375 |
| log loss | 1.734 | 1.739 |
| in-sample accuracy | 1.000 | 1.000 |
| seed sensitivity | none observed (logreg) | accuracy 0.375 → 0.625 at seed 7 |

Two honest readings:

1. **Neither model has learned this task.** The binary model scores 0.375 against
   a 0.500 floor — *worse than a constant classifier* — after fitting its twenty
   training rows perfectly. The six-class model lands at 0.208 against a 0.167
   floor: 0.042 above it, which is exactly one eval row out of 24, i.e. noise.
   Neither number is a result.
2. **That is a property of the fixture, not a bug in the pipeline.** Every one of
   the 84 rows is a distinct hand-written sentence, so a held-out row shares only
   incidental vocabulary with training rows; a bag-of-n-grams model with 20 rows
   has nothing to transfer. `train_accuracy = 1.000` is the evidence that
   features, labels, fitting and prediction are wired correctly end to end.

## What the smoke run is therefore allowed to claim

* The data pipeline reproduces byte-for-byte (`dvc repro` + `dvc status`).
* The same config + seed produce **bit-identical** metrics (per-metric
  `==` after `pytest.approx(abs=1e-6)`), and a different seed moves them.
* The model still fits its own training rows (`train_accuracy = 1.000`); this is
  the canary that catches a broken training path, which held-out accuracy on 8
  rows cannot distinguish from noise.
* Every run records enough to be repeated: git commit, data hashes, config
  fingerprint, seed, library versions, artefacts.

It claims **nothing** about emotion recognition, and no number here should ever
appear in a model card as a result.

## Why `majority_accuracy` is logged on every run

Day 31 added `majority_accuracy` (the score of "always predict the most common
eval label") because without it 0.375 reads like a bad model rather than a
meaningless one — and, on a real corpus, "92 % accuracy" on a 92 %-negative set
reads like success. It is computed from the eval labels, so it is a property of
the data, not of the model, and it is not part of a config's metric list.

## The exploration that led here (superseded configs, same conclusion)

* 18 combinations of model kind × TF-IDF settings × 2 seeds, evaluated on the
  six-class slice: accuracy 0.167–0.292. The best was a three-class subset at
  0.292, i.e. still chance.
* Multi-class tuning is not a matter of more passes: `max_iter` 400 + `lbfgs`
  (the multiclass config) fits the training rows perfectly and changes nothing on
  the eval rows.
* Therefore the smoke config was moved to the binary slice, which at least has a
  floor a human can compare against, and the canary that matters was made
  explicit (`train_accuracy`, plus the committed baseline).

## Reproduce

```bash
cd ml
python -m dvc repro
python -m manovia_ml.training.train --config configs/toy_emotion_multiclass_logreg.yaml
python -m manovia_ml.training.train --config configs/toy_emotion_sgd.yaml
python -m manovia_ml.training.train --config configs/toy_emotion_sgd.yaml --seed 7
```

The third command is the seed-sensitivity check the reproducibility tests assert:
the metrics move, and because the baseline was recorded at seed `20261013` the
run says so and skips the baseline comparison (a seed change is an act, not
drift).
