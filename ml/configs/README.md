# Configs

One YAML file per experiment. Everything a run can vary lives here; nothing the
schema rejects is allowed to reach the code (`manovia_ml/config.py`, pydantic v2,
`extra="forbid"`).

| file | what it is | runs where |
| --- | --- | --- |
| `toy_emotion_sgd.yaml` | smoke + reproducibility config: binary `joy`/`anger` slice, seed-sensitive SGD | here, and CI on every push |
| `toy_emotion_multiclass_logreg.yaml` | the six-class shape on the full fixture: four language codes, confusion matrix, deterministic logreg | here (`make ml-train CONFIG=configs/toy_emotion_multiclass_logreg.yaml`) |
| `colab_goemotions_logreg.yaml` | first real run: GoEmotions, English, multi-label collapsed to one label | Colab/Kaggle notebook (`ml/notebooks/`) |
| `hi_bn_handwritten_logreg.yaml` | **planned** — names data that does not exist yet | after native review (see the catalogue) |
| `expected/<name>.json` | the recorded baseline for `<name>.yaml`: metrics + run id + seed | written by `make ml-record`, checked by `make ml-train` |

## Fields

```yaml
name:      # required, gives the MLflow run its name and the baseline its file
seed:      # required, 0 <= seed < 2**32 — the single source of stochasticity
notes:     # free text, logged with the run
threads:   1 | null   # pin BLAS/OpenMP pools (1 = reproducible everywhere)

data:
  train:  {path: ..., sha256: ..., rows: ...}   # sha256/rows optional but
  eval:   {path: ..., sha256: ..., rows: ...}   # recommended: they fail closed
  text_field / label_field / lang_field
  labels: [joy, sadness, ...]                   # explicit class order

model:
  kind: tfidf_sgd | tfidf_logreg
  tfidf: {word_ngram_range, char_ngram_range, min_df, max_features, sublinear_tf, lowercase}
  # tfidf_sgd:    alpha, max_iter, average, class_weight
  # tfidf_logreg: C, max_iter, solver (lbfgs | newton-cholesky), class_weight

evaluation:
  tolerance: 1.0e-6           # how far two same-seed runs may drift
  metrics: [accuracy, macro_f1, weighted_f1, log_loss]
  confusion_matrix: true

tracking:
  experiment: manovia-ml-...
  tracking_uri: null          # null → MLFLOW_TRACKING_URI → file:<ml root>/mlruns
  log_environment: true
  run_name: null
  tags: {purpose: smoke}
```

## Metrics: the two that are always logged

A config lists the metrics it wants, and two more are logged on **every** run
because they are properties of the data and the sampler rather than choices:

- `train_accuracy` — in-sample accuracy. On the toy fixture this is the canary:
  a pipeline that can no longer fit its own rows is broken in a way that held-out
  accuracy on 8 rows cannot distinguish from noise (`ml/findings/`).
- `majority_accuracy` — the score of "always predict the most common eval label",
  i.e. the floor accuracy has to clear. Without it, 0.375 on a 4+4 split reads
  like a bad model instead of a meaningless number.

`log_loss` is the one metric that needs probabilities, so it is opt-in: add it to
`metrics:` only for models that provide `predict_proba` (`tfidf_sgd` with
`average: false`, or `tfidf_logreg`). Asking for it on a model that cannot produce
probabilities is a `ConfigError` at the start of the run, not a zero at the end.

## Conventions

- **`--seed N` overrides the seed deliberately** and prints a note that the
  baseline check is skipped: a seed change is an act, not drift. The baseline
  gate applies to the seed recorded in `expected/<name>.json`.
- **`toy_emotion_sgd.yaml` is referenced by CI.** Changing its metrics means
  re-recording the baseline (`make ml-record CONFIG=configs/toy_emotion_sgd.yaml`)
  and saying why in the PR — the diff on `expected/*.json` is the review.
- **A config that cannot be run in this sandbox says so** at the top, and the
  reason (network, no GPU, data not written yet). Nothing here pretends to have
  been executed when it was not.
- **`sha256` pins are literal.** If the data moves, the run stops and tells you
  both hashes; it never warns and continues. Updating a pin is a deliberate edit
  that shows up in review.
- **Never put a path to a `DO NOT USE` dataset in a config.** A test enforces it
  (`ml/tests/test_catalogue.py`), because that is the rule most likely to lose to
  a deadline.
