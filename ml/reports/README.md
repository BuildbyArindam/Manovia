# Reports

Generated, reviewed, committed. A report is the only part of a run that belongs
in Git: it holds numbers and hashes, never rows and never weights
(`make ml-report RUN=<id>` writes one, `ml/reports/smoke/<date>-smoke.md` is the
smoke run's).

| path | what it is |
| --- | --- |
| `smoke/<date>-smoke.md` | the CI smoke run's report: config fingerprint, commit, data hashes, seed, metrics. Committed so a green CI job leaves a trace that can be read without opening MLflow. |
| `<run id>.md` | a real run's report (written and committed by hand when a number is going to be quoted in a PR, a doc or the product card). |

## What these numbers may be used for

- **The smoke report** answers one question: *does the platform still work?* The
  model is trained on 20 hand-written rows for two classes; its accuracy is a
  canary, not a quality claim. `train_accuracy = 1.000` is the interesting
  number there — it says features, labels and fitting are still wired together.
- **A GoEmotions or hi/bn report** may be quoted in a model card, a PR or the
  repository's evaluation docs — with its run id, its data hash, its split sizes
  and its limitations. Never without them.
- **Nothing here is clinically validated.** No number in this directory is
  evidence that Manovia works for a person in distress; that review is
  [docs/clinical-review.md](../../docs/clinical-review.md).

## Regenerating

```bash
make ml-report RUN=<run id>            # writes reports/<run id>.md
make ml-smoke                          # trains the smoke model, checks the baseline, writes today's report
```

The report is rendered from the run's own MLflow record (`params`, `metrics`,
`data.json`, `environment.json`, `expected.json`), so it cannot disagree with the
run it names — if it did, the fix is to re-generate it, not to edit it.
