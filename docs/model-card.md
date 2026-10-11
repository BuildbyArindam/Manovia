# Model card — screening instruments, safety models and language models

Manovia is a self-help companion, not a medical device, and this card exists so
that the two kinds of third-party component it depends on can be audited without
reading the source: the **screening instruments** it reproduces verbatim, and the
**models** (safety classifier, language models) that run around them. Everything
below was checked on the date shown; anything not verified is marked as such.

## Screening instruments (Day 20)

Both instruments are reproduced **unaltered** for a person's own self-help
screening. Manovia does not modify them, does not relicense them, and is not
affiliated with, endorsed by or sponsored by Pfizer Inc. or the instruments'
developers.

### PHQ-9 — Patient Health Questionnaire-9

| | |
| --- | --- |
| Developers | Drs. Robert L. Spitzer, Janet B.W. Williams, Kurt Kroenke and colleagues, with an educational grant from Pfizer Inc. |
| Rights holder | Copyright © 1999 Pfizer Inc. All rights reserved. PRIME-MD® is a trademark of Pfizer Inc. |
| Licence / availability | **"No permission required to reproduce, translate, display or distribute."** The official English sheet is published free of charge for clinical, research and educational use. |
| Source (item wording) | <https://www.phqscreeners.com/images/sites/g/files/g10060481/f/201412/PHQ-9_English.pdf> |
| Source (scoring) | <https://www.phqscreeners.com/images/sites/g/files/g10016261/f/201412/instructions.pdf> — "Scores of 5, 10, 15, and 20 represent cutpoints for mild, moderate, moderately severe and severe depression, respectively." |
| Items / range | 9 items, each 0–3; total 0–27 |
| Bands | 0–4 minimal · 5–9 mild · 10–14 moderate · 15–19 moderately severe · 20–27 severe |
| Safety rule | Item 9 (thoughts of being better off dead or of hurting yourself) answered **above zero** triggers the crisis flow regardless of the total. |
| LOINC | `44261-6` — Patient Health Questionnaire 9 item (PHQ-9) total score [Reported] |
| Citation | Kroenke K, Spitzer RL, Williams JB. *The PHQ-9: validity of a brief depression severity measure.* J Gen Intern Med. 2001;16(9):606–613. |
| Wording verified | 2026-10-11, character by character, against the official PDF above. |

### GAD-7 — Generalized Anxiety Disorder-7

| | |
| --- | --- |
| Developers | Drs. Robert L. Spitzer, Janet B.W. Williams, Kurt Kroenke and colleagues, with an educational grant from Pfizer Inc. |
| Rights holder | Copyright © Pfizer Inc. All rights reserved. |
| Licence / availability | **"No permission required to reproduce, translate, display or distribute."** The official English sheet is published free of charge for clinical, research and educational use. |
| Source (item wording) | <https://www.phqscreeners.com/images/sites/g/files/g10060481/f/201412/GAD-7_English.pdf> |
| Source (scoring) | <https://www.phqscreeners.com/images/sites/g/files/g10016261/f/201412/instructions.pdf> — "Scores of 5, 10, and 15 represent cutpoints for mild, moderate, and severe anxiety, respectively." |
| Items / range | 7 items, each 0–3; total 0–21 |
| Bands | 0–4 minimal · 5–9 mild · 10–14 moderate · 15–21 severe |
| Safety rule | No suicidality item; no crisis path. |
| LOINC | `69737-5` — Generalized anxiety disorder 7 item (GAD-7) panel · `70274-6` — GAD-7 total score [Reported.PHQ] |
| Citation | Spitzer RL, Kroenke K, Williams JB, Löwe B. *A brief measure for assessing generalized anxiety disorder: the GAD-7.* Arch Intern Med. 2006;166(10):1092–1097. |
| Wording verified | 2026-10-11, character by character, against the official PDF above. |

The machine-readable form of all of the above — including the availability note,
the exact scoring-source quotes and the non-affiliation disclaimer — is the
`provenance` block of `backend/app/content/assessments/phq9.json` and
`gad7.json`, which the API publishes and both the result page and the printable
summary display.

**Band copy and the phrase "screening result, not a diagnosis" are written by
this project, not by the instruments' authors**, and have not been reviewed by a
clinician. The instrument contributes the items, the response set, the scoring
rule and the cutpoints; everything Manovia says *about* a score is Manovia's.

## Emotion / safety classifier

| | |
| --- | --- |
| Model | `SamLowe/roberta-base-go_emotions` (GoEmotions 27 labels + neutral, ~125M parameters) |
| Where it runs | Only the user's own API process; input text is never sent to a third party for this path. |
| Licence | **MIT** — verified from three independent sources (a model-card mirror, a model registry carrying `license:mit`, a hosted-endpoint catalogue). Recorded in [ADR 0006](adr/0006-emotion-model.md). |
| Training data | Google Research GoEmotions (Demszky et al., 2020), generally distributed as Apache-2.0 — **not verified from this sandbox**; confirm before any commercial use. |
| Optional | The model is an optional extra (`nlp`); without it the deterministic lexicon fallback runs. |
| Known limit | The classifier rates some ordinary exam-related phrases HIGH. The verdict is honoured, never suppressed; see [Known issues](../PROGRESS.md#known-issues). |

## Language models

Manovia talks to providers (Anthropic, OpenAI, a local Ollama, or a scripted
`fake` provider) behind one interface, configured only by environment variables;
none is vendored and no provider is the default in tests. Prompts carry
redacted, identifier-free text, and every reply passes the deterministic output
guard before it reaches a person. Provider terms and data-handling policies are
the provider's, not Manovia's; the `fake` provider exists so the whole product
can be developed and tested offline.

## Manovia's own code

The repository is MIT-licensed (see `LICENSE`). The scoring rules, content
files, safety rules, copy and UI in this project are ours; the instruments and
models above are not.
