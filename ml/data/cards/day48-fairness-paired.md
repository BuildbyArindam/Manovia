# Dataset card — Fairness paired-sentence templates (Day 48)

| | |
| --- | --- |
| Card version | 1.0 (2026-10-20) |
| Status | frozen |
| Catalogue entry | `ml/data/CATALOGUE.md` § 18 (new) |
| Owner / authors | Manovia maintainers |
| Licence | MIT (repository LICENSE) |
| Licence evidence | LICENSE in repo root, read 2026-10-20 |
| Last verified | 2026-10-20 |

## 1. What it is

Hand-written paired-sentence templates that swap a single identity term (religion, caste, gender, disability, sexuality, region, language, script, code-mixing, formality, dialect) while keeping the rest of the sentence identical. Each pair tests that the model's prediction does not change materially. The dataset is not labelled with human judgments — the expected answer is invariance.

## 2. Provenance

| | |
| --- | --- |
| Origin | synthetic, authored in `ml/src/manovia_ml/fairness/templates.py` |
| Collection date | 2026-10-20 |
| Collection method | Hand-written by maintainers against the task brief's list of axes |
| Personal data | no |
| Consent basis | n/a — synthetic |
| Redistribution | MIT; committed as code, not as data |

## 3. Contents

| | |
| --- | --- |
| Languages | en, hi, hi-Latn, bn (templates contain Devanagari and Bengali variants) |
| Size | 17 templates, 53 variant strings (see `TEMPLATES` list) |
| Label scheme | None — the test is gap = max absolute difference across variants in [0,1] |
| Label quality | n/a |
| Fields | `id`, `category`, `base` (with `{identity}` slot), `variants` |
| Hashes | Template list is code; versioned by git commit. No DVC. |

## 4. Known biases

- Templates are written by one team; real identity phrasing in India is broader.
- Only one base per category for some axes; coverage is shallow.
- Religion/caste templates list specific groups; many other groups absent.
- Dialect spelling templates are illustrative, not exhaustive.

## 5. Fitness for Manovia

| use | allowed? | notes |
| --- | --- | --- |
| Training a shipped model | no | Evaluation only — must not be trained on |
| Evaluation / benchmark | yes | Fairness gate; fails CI when exceeded |
| Research comparison only | n/a |  |
| Committed to Git | yes | Small fixture; this card + code |

## 6. Handling

- Storage: committed as code (`ml/src/manovia_ml/fairness/templates.py`)
- Retention: versioned forever
- Access: public in repo

## 7. Splits

| split | rows | how chosen | may it be changed? |
| --- | --- | --- | --- |
| all | 17 templates | seed-independent | yes, with audit reason; thresholds re-checked |

## 8. Review record

- Created 2026-10-20 for Day 48 audit. Native-speaker review of templates pending.
