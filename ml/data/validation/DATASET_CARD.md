# Dataset card — Manovia multilingual emotion set (hand-written, v1)

> Written from [`ml/templates/DATASET_CARD.md`](../../templates/DATASET_CARD.md).
> This is the *held-out* set for the Day-32 LoRA emotion model: it is never
> trained on, and it is never used to tune thresholds (that happens on the
> model's own validation split). It is loaded through the same code path as any
> other split (`manovia_ml.training.emotion.data.load_validation`).

| | |
| --- | --- |
| Name | Manovia multilingual emotion set (held-out) |
| Version | v1, file `ml/data/validation/emotion_validation.jsonl` |
| sha256 | printed by `make ml-emotion-summary` / `python -m manovia_ml.training.emotion.data hash <path>` |
| Rows | 174 (58 en, 58 hi, 58 bn) |
| Labels | Manovia's nine: joy, sadness, anger, fear, anxiety, shame, loneliness, calm, neutral (multi-label allowed) |
| Licence | MIT — this repository's `LICENSE`. Authored here; no third-party text. |
| Personal data | **No.** Every row is invented. No real message, transcript, review or scrape is used, quoted or paraphrased from any source. |
| Source | Hand-written in this repository by the project author (an LLM assistant drafted the sentences on the author's instruction and the author owns the labels). |
| Native review | **Not yet reviewed.** See "Review status" below — this is the most important limitation on this card. |
| Catalogue entry | `ml/data/CATALOGUE.md` § 14 (Manovia hi/bn emotion set) and § 13 (Manovia fixtures) |

## 1. What it is for

The final evaluation of the Day-32 model: macro-F1 overall, per label and **per
language**, the comparison against the keyword and off-the-shelf baselines, and
the error analysis. It is deliberately *not* a random sample of internet text:
it is 174 sentences written the way the three target registers are actually
typed, with the labels the product uses.

## 2. Why a new set, when `evals/datasets/emotion_cases.jsonl` exists

`evals/datasets/emotion_cases.jsonl` (162 rows) is the *product* evaluation set:
it is what the shipped keyword chain's thresholds were developed against, and it
is quoted in `evals/reports/emotion_*.md`. Scoring a newly trained model against
a set the baseline was tuned on would give the baseline an advantage nobody can
quantify. That is why this file is a second, independent, hand-written set with
different sentences, and why the comparison in the Day-32 report is run on this
one only.

Two sets that both come from the same author are still one author's view of what
an emotion sounds like. That limitation is real and is repeated in the model
card.

## 3. Composition

| register | what it looks like | why it is there |
| --- | --- | --- |
| direct first person | "I told everyone the work was finished and now I have to admit it is not" | the common case |
| indirect / situational | "the lift stopped between two floors and the light flickered" | Day-24 finding: the keyword chain's misses are indirect expressions |
| everyday neutrality | "the shop is on the second floor next to the pharmacy" | `neutral` is 1/9 of the taxonomy and is where the offline baseline is weakest (F1 0.464 in `evals/reports/emotion_2026-10-13.md`) |
| multi-label (12 rows) | "the plane dropped suddenly and my hands kept shaking for the rest of the flight" | fear **and** anxiety; single-label scoring would punish a model that is right |

Label counts per language are printed by `make ml-emotion-summary` and quoted in
the run's data summary; no row is duplicated across languages.

## 4. Known limitations

- **One author, no native-speaker review yet.** `translations/STATUS.md` says the
  same thing about the product copy; the Hindi and Bengali rows here have the
  same problem. Treat every per-language number in the Day-32 report as
  *indicative of the plumbing*, not as a measurement of Hindi or Bengali emotion
  recognition. The plan in `ml/data/CATALOGUE.md` § "Hindi and Bengali emotion
  data" (two independent native reviewers per language) applies to this file.
- **No code-mixed Hinglish (`hi-Latn`) rows.** The brief for Day 32 asks for
  en/hi/bn; `hi-Latn` is a *parking-lot* item, and the model card says so.
- **Register skew.** Deliberately short, single-situation sentences. Real
  messages are longer, referential and chained across turns.
- **No clinical or crisis content, by design.** Nothing here tests whether the
  model would notice severe distress — that is the crisis gate's job, and
  `ml/` never touches it (AGENTS.md ML ground rule 1). A model trained with this
  data must not be used as a safety signal.
- **Not a benchmark.** 174 rows give 95% confidence intervals roughly ±0.07 on
  macro-F1; the report prints the intervals rather than pretending otherwise.

## 5. Review status

| item | state |
| --- | --- |
| sentences written | yes (v1) |
| labels assigned by the author | yes (v1) |
| second pass over the labels by a different reader | **no** |
| native-speaker review (hi, bn), two reviewers, agreement recorded | **no** |
| adjudication record for disagreements | n/a yet |

Until the last three are done, this set is used for **engineering verification**
and the numbers it produces are labelled *provisional* in
[`docs/model-card.md`](../../../docs/model-card.md).

## 6. Provenance and change control

- The file is committed (it contains no personal data, and it is small) and its
  sha256 is recorded in every run that reads it.
- Editing a row is a **new version**: bump the `source` field
  (`handwritten-val-v2`), re-run the overlap check against the training data, and
  re-state the numbers. Rewriting history in place is how a metric quietly stops
  being reproducible.
- `manovia_ml.training.emotion.data overlap <train> <this file>` must report zero
  exact and zero near-duplicates before any number from it is quoted.
