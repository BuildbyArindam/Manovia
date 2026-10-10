# Evaluation datasets

Reserved for versioned, privacy-reviewed evaluation fixtures. Do not add real
user conversations or identifying data.

## Crisis detection dataset (Day 9)

`crisis_cases.jsonl` — 572 synthetic labelled cases for the safety risk
classifier and ensemble. Fields: `id`, `text`, `lang` (`en`, `hi`, `hi-Latn`,
`bn`), `label` (`none` | `low` | `medium` | `high` | `imminent`), `category`,
`notes`, `difficulty`. No case names a method or means (enforced by the
builder, `evals/build_crisis_dataset.py`, and by `tests/safety/test_eval_dataset.py`).

The families the labels must be tested against:

- **hard_negative.figurative** — "this deadline is killing me"
- **hard_negative.news** — reporting an event, not experiencing one
- **hard_negative.media** — discussing a song or film, with quoted lyrics
- **hard_negative.third_person** — worried about somebody else
- **hard_negative.recovery** — past-tense "I was there once; here is what helped"
- **hard_positive.indirect** — crisis expressed with none of the obvious words

### Splits and the frozen test set

`manifest.json` records the deterministic split (seed 20261010):
`train` / `dev` / `test` = 340 / 116 / 116, plus each file's SHA-256 and
`frozen: true` for the test split.

- **train** feeds `evals/train_safety_classifier.py` (the committed artifact).
- **dev** feeds threshold tuning (`evals/tune_safety_thresholds.py`) and is the
  default split for `evals/run_safety_eval.py`.
- **test** is frozen: `run_safety_eval.py --split test` refuses to score a file
  whose hash differs from the manifest, and the backend test suite asserts that
  neither the training script nor the tuner so much as names the test file.
  Thresholds are never chosen against it; it is scored once, in the Day 9
  verification section.

### Regenerating

```bash
cd backend
../.venv/bin/python ../evals/build_crisis_dataset.py   # rewrites all four files + manifest
../.venv/bin/python ../evals/run_safety_eval.py --split dev
```

Regeneration changes case ids and the split hashes; re-tune thresholds on the
new dev split before trusting any number, and re-freeze.
