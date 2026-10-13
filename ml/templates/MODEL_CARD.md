# Model card — `<model name / run id>`

> Template (Day 31). This is the per-run card the ML platform demands before a
> number from a run is quoted anywhere. It is *not* a replacement for the
> product-level card ([docs/model-card.md](../../docs/model-card.md)), which
> describes every component that decides what a person sees; a model that would
> ship also needs an entry there. Copy this file to
> `ml/reports/<config>/<run-id>-card.md` and fill in from the run itself
> (`make ml-report RUN=...` prints most of the fields).

| | |
| --- | --- |
| Card version | `0.1` (`<date>`) |
| Status | `experimental \| candidate \| rejected \| retired` |
| Run id | `<MLflow run id>` |
| Config | `<ml/configs/....yaml>` (fingerprint `<config.sha256>`) |
| Git commit | `<commit>` (dirty: `<true/false>`) |
| Seed | `<seed>` |
| Trained | `<date>` on `<hardware, CPU/GPU>` for `<wall-clock>` |
| Owner | `<who>` |

## 1. What it is

_Two sentences: architecture, and the exact task it performs. Include the
artefact path and its library versions (`scikit-learn 1.9.1`, …) because those
decide whether it can be loaded at all (see `manovia_ml/serving/artifacts.py`)._

## 2. Intended use

| | |
| --- | --- |
| Primary use | `<e.g. a tone hint next to a reply — never a safety decision>` |
| Users | `<who consumes the output, human or code>` |
| Out of scope | `<what must never call it; for Manovia: the crisis gate is rules-first and this model may only raise a level, never lower one>` |
| Failure mode if wrong | `<what a person would see>` |

## 3. Training data

| | |
| --- | --- |
| Dataset(s) | `<name → ml/data/CATALOGUE.md § n, dataset card path>` |
| Version | `<sha256 of each split (from the run's data.json)>` |
| Rows | `<train / eval / test>` |
| Label scheme | `<labels, and the mapping from any source scheme>` |
| Labels thrown away | `<what the mapping dropped, and why>` |

## 4. Evaluation

| metric | value | split | n | notes |
| --- | --- | --- | --- | --- |
| `<accuracy>` | `<value>` | `<eval/test>` | `<rows>` | `<baseline to compare against, e.g. majority class = x>` |
| `<macro_f1>` | | | | |
| `<log_loss>` | | | | |
| `<train_accuracy>` | | train | | an overfit check, not a quality claim |

Confusion matrix: `<link or the run's evaluation.json>`.
Reproduction: `make ml-train CONFIG=...` → `make ml-eval RUN=<id>` →
`make ml-report RUN=<id>`; the committed baseline is
`ml/configs/expected/<config>.json`.

_State the sample size honestly. Under ~100 eval rows, most of these numbers are
inside the noise, and the card should say so instead of leaving the reader to
work it out._

## 5. Known limitations and biases

- `<what the data cannot represent (register, language, population)>`
- `<labels the model cannot output or confuses systematically>`
- `<domains it was never evaluated on>`
- `<where it is known to fail — link the failing case if there is one>`

## 6. Fairness across the languages Manovia supports

| language | trained on | evaluated on | metric | notes |
| --- | --- | --- | --- | --- |
| en | | | | |
| hi | | | | |
| hi-Latn | | | | |
| bn | | | | |

_A model with no Hindi/Bengali training rows must say **not supported** here
rather than being shipped silently in a multilingual product._

## 7. Safety relationship

| question | answer |
| --- | --- |
| Can it lower a safety level? | **No** (design rule; state where it is enforced) |
| Can it see raw user identifiers? | **No** |
| Is it called at HIGH/IMMINENT? | **No** |
| What checks its output? | `<the deterministic guard / none — and if none, why that is acceptable>` |
| Reviewed by a clinician? | `<not yet — and therefore it may not be described as clinically validated>` |

## 8. Decision

| | |
| --- | --- |
| Ship? | `<no / behind flag X / yes>` |
| Decided by | `<who, when>` |
| Revisit when | `<what would change the answer>` |
| Known blockers | `<native review, licence, data, clinical sign-off>` |
