# ADR 0032: Fine-tuning the multilingual emotion model (LoRA on XLM-R, nine-label taxonomy)

- Status: accepted (Day 32)
- Date: 2026-10-13
- Deciders: Manovia maintainers
- Supersedes: none. Supplies the evidence [0006 emotion model](0006-emotion-model.md) asked for and extends it with a *trained* option
- Related: [0009 safety ML ensemble](0009-safety-ml-ensemble.md), [0023 multilingual](0023-multilingual-english-hindi-bengali.md), [0024 evaluation harness](0024-evaluation-harness.md), [0028 model card and clinical review](0028-model-card-and-clinical-review-gate.md), [0031 ML platform](0031-ml-platform.md), [`ml/data/CATALOGUE.md`](../../ml/data/CATALOGUE.md), [`docs/model-card.md`](../model-card.md)

## Context

The product's emotion analyzer is the off-the-shelf
`SamLowe/roberta-base-go_emotions` checkpoint (ADR 0006): 28 English GoEmotions
labels folded onto Manovia's nine, trained on Reddit comments. Three problems
that Day 6 accepted as limits are now the reason for this ADR:

1. **It is English-only, in an en/hi/bn product.** Hindi and Bengali input is
   scored by an English model over a tokenizer that was not trained for those
   scripts. The Day-6 evaluation's per-language spreads (macro-F1 0.833 hi vs
   0.774 bn) come from *that* model, and the tokenizer is the bottleneck, not the
   classifier head.
2. **Its label space is not Manovia's.** `loneliness` does not exist in
   GoEmotions; `nervousness` has to stand in for `anxiety`; `grief`, `remorse`
   and `embarrassment` have to be folded by hand. The mapping is documented and
   tested, but it is a translation of somebody else's taxonomy.
3. **It cannot be improved.** When the Day-27 evaluation showed that calm and
   neutral rows were the weakest, there was nothing to fix: the checkpoint's
   weights are not ours and its training data is not recollectable.

The brief for Day 32 is to fine-tune a multilingual emotion classifier into the
nine-label taxonomy with LoRA, compare two base models, keep a hand-written
en/hi/bn set out of training, tune per-label thresholds on validation, evaluate
with bootstrap confidence intervals against the Day-6 baseline, package the
result, and put it behind the existing `EmotionAnalyzer` interface with
automatic fallback.

Constraints, unchanged from Day 31: **no GPU and no Hugging Face access in the
build sandbox**; no dataset rows or model weights may ever be committed; every
dataset must be in the catalogue with a verified licence before use; every run
logged to MLflow; config is data; the safety path may not be affected in any way
by a change in the emotion model.

## Decision

### 1. Base model: `FacebookAI/xlm-roberta-base` (MIT) is the default

Both candidates were verified through the Hub API on 2026-10-13 and recorded in
the config's header:

| | `xlm-roberta-base` | `google/muril-base-cased` |
| --- | --- | --- |
| licence | **MIT** | **Apache-2.0** |
| revision (sha) | `e73636d4f797dec63c3081bb6ed5c7b0bb3f2089` | `afd9f36c7923d54e97903922ff1b260d091d202f` |
| parameters | 278,885,778 (F32, `model.safetensors`) | BERT-base, `pytorch_model.bin` only |
| languages | ~100, including en/hi/bn | 17 Indian languages (hi, bn, en among them) |
| pretraining | CC-100 etc., general multilingual | translated + transliterated Indic segment pairs |
| last modified | 2024-02-19 | 2022-06-10 |

**XLM-R is the default because the product is not only Indic**: Hindi and Bengali
are the languages Manovia must be *best* at, but the taxonomy and the safety
copy also serve English and any future language, and MuRIL's own card says it "is
not expected to perform well on languages other than the ones used in
pretraining". XLM-R is one model for en/hi/bn/world instead of an Indic
specialist that degrades for everyone else, and its tokenizer covers Devanagari
and Bengali natively — which is exactly the bottleneck named in the Context.

**MuRIL stays as the comparison, not as dead code:**
`ml/configs/emotion_lora_muril.yaml` is the same run against
`google/muril-base-cased`, and the evaluation table in the PR is the three-way
(keyword, Day-6 off-the-shelf, fine-tuned) comparison per language. If MuRIL
wins on hi/bn by a margin that survives the confidence intervals, that is
evidence for a second, Indic-specialised model later — decided by a number, not
by preference. Both licences are permissive, so the choice is a quality
question, not a legal one; had either been non-commercial, the catalogue's rules
would have decided it for us.

### 2. The taxonomy mapping is one documented, tested table

`ml/src/manovia_ml/training/emotion/taxonomy.py` is the only place that knows how
a source label maps onto Manovia's nine. It is a **weighted, many-to-many** table
(`nervousness → anxiety 1.0`, `embarrassment → shame 1.0`, `remorse → shame 0.9`,
`relief → calm 0.9`, `disgust → anger 0.8`, `amusement → joy 1.0`, …), with the
rationale for each row in a docstring, and the weights become **soft targets** in
training rather than a hard relabel: `{annoyance}` is 0.6 anger, not 1.0 anger
and not a dropped row. `neutral` is **derived** — a row is neutral when nothing
else it carries clears the threshold — which is the product's meaning of
`neutral` ("the analyzer looked and found nothing"), not an eleventh emotion.

Two consequences are stated loudly because they shape every number the model
will ever produce:

- **GoEmotions has no supervision for `loneliness`.** `unreachable_targets()`
  returns it, the run manifest carries it
  (`labels_unreachable_from_training_data`), and every report says it: the
  fine-tuned model cannot learn loneliness from GoEmotions, only inherit
  nothing. EmoNoBa (Bengali) can teach anger/fear/joy/sadness but not
  anxiety/shame/loneliness/calm either.
- **Dropped rows are counted, not hidden.** `{surprise}` and `{confusion}` rows
  are dropped where the table deliberately drops them (an emotion Manovia does
  not have is not "neutral"), and the converter reports `rows_in`,
  `rows_with_emotion_target`, `rows_neutral_only` and the per-label drop counts.
  A conversion that quietly loses half a corpus is how a model ends up
  describing a dataset nobody checked.

### 3. There is a hand-written, committed, held-out set — and it is not the toy fixture

`ml/data/validation/emotion_validation.jsonl` (174 rows: 58 each en/hi/bn, 12
multi-label, all nine labels present, sha256
`fb116c7c…1b08c`) is written for this day, with its own
`DATASET_CARD.md`. This is a deliberate, narrow exception to the Day-31 rule that
the only committed JSONL is the toy fixture:

- it contains **no third-party data and no personal data** — the rows are
  invented situations, written by the maintainers, MIT-licensed like the rest of
  the repository;
- it is the *evaluation* set, and a per-language number needs data the corpus
  does not have. Without it there is no hi or bn number at all, only a promise;
- it stays held out: `data.py` fails the run if training rows and this set share
  even a near-duplicate sentence (exact match *and* two near-duplicate rules),
  and the same check is a re-run step in the verification table.

Its card says what it is not: **no native-speaker review yet** (the maintainer
who wrote the hi/bn rows is not a native reviewer), no Romanised (`hi-Latn`)
rows, and 174 rows is enough for a direction, not for a claim.

### 4. Training choices, and where the tuned knobs are allowed to look

- **Soft targets + `BCEWithLogitsLoss`** with per-label `pos_weight`
  (neg/pos, capped at 50) computed from the *training* split only, label
  smoothing 0.02, cosine schedule with 6% warmup, gradient clipping at 1.0.
- **LoRA** (rank/alpha/dropout/target modules in the config; default rank 16,
  alpha 32, dropout 0.1 on `query` and `value`), base frozen. Mixed precision is
  `auto`: fp16 on CUDA, fp32 on CPU, and the run logs which one it got.
- **Thresholds are tuned on the validation split, never on the held-out set**:
  a grid scan per label maximising F1, a label with fewer than `min_support`
  positives keeps the 0.5 default *with the reason recorded* in
  `thresholds.json`. The tuned value is used by `asserted_labels()`; the
  `EmotionResult.primary` stays the argmax, because the schema defines it that
  way and rescaling a distribution to make a label win would be a lie in a
  different shape.
- **The Optuna sweep is ≤ 12 trials over two knobs** (LoRA rank, learning rate),
  each trial a full training run under a nested MLflow run, scored on
  validation macro-F1 at default thresholds. More knobs, or tuning thresholds
  inside the sweep, would be fitting the split that is choosing the sweep.

### 5. Evaluation that can say "not better"

`ml/src/manovia_ml/training/emotion/evaluate.py` is **inference-only**: it
refuses to run in a process that imported the training module, so a number
quoted in a report cannot have come from anything but the saved artefact.
It reports macro-F1, per-label and per-language F1, micro-F1, subset accuracy,
calibration (ECE + reliability bins), the primary-label confusion matrix,
row-seeded bootstrap 95% intervals, and a top-20 error analysis grouped by
cause (including `untrained_language` and `neutral_absorbed_a_positive`).
The report's comparison table prints the keyword chain and the Day-6
off-the-shelf checkpoint beside the fine-tuned model **per language**, and the
renderer emits an explicit sentence for every language where a baseline wins.
A model card that cannot contain that sentence is not a model card.

### 6. Packaging, serving and the fallback

The training run writes one directory (never committed):

```
model/            merged weights + config (no PEFT adapter to load)
tokenizer/        the tokenizer trained with them
thresholds.json   per-label thresholds + the split they were tuned on
manifest.json     base model, labels, data hashes, seed, library versions, run id
MODEL_CARD.md     written from ml/templates before any number is quoted
```

The backend chooses it with `EMOTION_ANALYZER=trained` and
`EMOTION_TRAINED_DIR=<directory>`; `baseline` names the Day-6 hub checkpoint
explicitly so a report can compare against it by name. `TrainedEmotionAnalyzer`
is lazy, thread-safe, and **never fatal**: a missing directory, a missing
`torch`/`transformers`, or a corrupt file becomes one warning and the existing
lexicon fallbacks answer (ADR 0006's chain, unchanged). Deleting the weights is a
supported state.

**The emotion model has no safety role.** It is a tone hint: it cannot lower a
risk level, and it is never called at HIGH or IMMINENT risk (ADR 0009). Fine-
tuning changes which tone is named; it does not touch the rules, the ensemble or
the crisis gate.

## Consequences

- The product can now run a model whose training data, mapping, thresholds and
  evaluation are all in this repository — and, just as importantly, can run
  *without* it: nothing about the day changes the behaviour of a deployment that
  keeps `EMOTION_ANALYZER=auto`.
- A number about this model carries a run id, and that run carries the git
  commit, the data hashes, the resolved config, the seed and the library
  versions (ADR 0031's seven things).
- `loneliness`, `calm` and `anxiety` are the labels this approach supervises
  worst (one unreachable from GoEmotions, one reachable only through `relief`,
  one only through `nervousness`). They are named in the model card and in the
  manifest rather than discovered later.
- Hindi and Bengali are evaluated, not supervised: a run on GoEmotions measures
  **transfer**. The number that matters for those languages still waits for the
  hand-written hi/bn corpus in `ml/data/planned/hi_bn_emotion/` with native
  review, which remains the honest answer to "is it good in Hindi?".
- The model inherits GoEmotions' biases: Reddit text, self-selected English
  internet users, and the class imbalance of a social-media dataset. The
  catalogue and the card both say so; the domain shift sentence is a required
  part of the card.
- Two things are deliberately *not* decided here: whether the model should ever
  be called on stored data, and whether a MuRIL variant ships alongside it. The
  first needs a privacy argument, the second needs the three-way comparison to
  be run on a GPU first.

## Verification

Run in the sandbox, on this tree:

| check | result |
| --- | --- |
| `pytest` in `ml/` (offline, tiny random-init model) | see the Day-32 verification table in `PROGRESS.md` |
| `make data` → toy splits | byte-identical to the recorded hashes |
| `python -m manovia_ml.training.emotion.train --config configs/emotion_lora_smoke.yaml` | MLflow run `e78feca4…`, ~6 s on CPU, artefact written |
| `python -m manovia_ml.training.emotion.evaluate --artifact <smoke artefact>` | fresh process, identical numbers, exit 0 |
| `EMOTION_ANALYZER=trained` against the smoke artefact | live: returns a nine-label result, falls back to the lexicon when the directory is removed |
| a real GPU run (`xlm-roberta-base`, GoEmotions) | **not run here** — no GPU, hub unreachable; `ml/notebooks/colab_emotion_lora.ipynb` is the ready-to-run path and the PR lists what the owner must check by hand |
