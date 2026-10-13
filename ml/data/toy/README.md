# The toy fixture

`toy_emotion.jsonl` — 84 short sentences, hand-written for this repository:
6 emotions (`joy, sadness, anger, fear, calm, neutral`) × 4 language variants
(`en` 30, `hi` 24, `hi-Latn` 18, `bn` 12).

| property | value |
| --- | --- |
| Personal data | **none** — invented everyday situations, no real messages, no names |
| Crisis content | **none** — a fixture that drifted into self-harm language would end up inside a mocked safety path, which is worse than useless |
| Licence | MIT (repository licence); authored in this repository |
| Rows | 84 (`{"id", "text", "lang", "label"}`), ~11 KB |
| Purpose | to let the training, tracking and reproducibility machinery run offline, on a CPU, in CI |
| Not | a benchmark. It is 84 sentences; nothing measured on it says anything about emotion recognition |

## Why a hand-written fixture instead of the real thing

The smoke run and the reproducibility tests need *some* data. A real corpus means
a network download, a licence review and a privacy decision (see
[`../CATALOGUE.md`](../CATALOGUE.md)), for a check that must run on every push in
seconds. A generated fixture would make "the same seed reproduces the same
metrics" a statement about the generator rather than about training, which is the
opposite of the point.

## What the two slices are for

The fixture is split by `manovia_ml.data.toy` (DVC stage `ml/dvc.yaml`):

| slice | classes | rows (train/eval) | used by | why |
| --- | --- | --- | --- | --- |
| `toy` | all six | 60 / 24 | `configs/toy_emotion_multiclass_logreg.yaml`, tests | the shape: six classes, four language codes, a real confusion matrix |
| `toy_smoke` | `joy`, `anger` | 20 / 8 | `configs/toy_emotion_sgd.yaml`, `make ml-smoke`, CI | binary labels give every eval row a 0.500 majority-class floor, so `majority_accuracy` makes "did this model learn anything?" answerable instead of guesswork |

**Known finding (2026-10-13):** on both slices the TF-IDF linear model fits its
training rows perfectly (in-sample accuracy 1.000) and is no better than a
constant classifier on held-out rows — 0.375 against a 0.500 floor on the binary
slice, and 0.208 against a 0.167 floor on the six-class one (one eval row of 24,
i.e. noise). Every row is a
distinct hand-written sentence, so held-out rows share only incidental vocabulary
with training rows and twenty of them are not enough to generalise. That is not a
bug, and it is not hidden: it is why `majority_accuracy` and `train_accuracy` are
logged on every run, why accuracy from this fixture is never quoted as a result,
and why the smoke gate is reproducibility rather than quality. Measurements:
[`../../findings/2026-10-13-toy-slice-baselines.md`](../../findings/2026-10-13-toy-slice-baselines.md).

## Changing the fixture

Editing a sentence changes the sha256 of the splits, which changes the pins in
`ml/configs/toy_emotion_sgd.yaml` and `toy_emotion_multiclass_logreg.yaml` and
invalidates the recorded baselines in `ml/configs/expected/`. That is intended:
the sequence is

```bash
make ml-data                                   # rewrite splits + dvc.lock
# update the sha256/rows pins in the configs to the new values (they are printed)
make ml-record CONFIG=configs/toy_emotion_sgd.yaml
make ml-record CONFIG=configs/toy_emotion_multiclass_logreg.yaml
```

and the diff on `expected/*.json` is the review. Requirements for a new row: no
personal data, no crisis content, everyday register, and the sentence must be
plausibly something someone would type into a wellbeing companion.
