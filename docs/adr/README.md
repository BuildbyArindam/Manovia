# Architecture decision records

Each ADR records one decision: the context, what was chosen, what it costs, and
what was rejected. The files are numbered in the order they were written, and a
number is never reused. Status values follow the ADRs themselves.

**Gap:** there is no `0016` in this directory. The number was never assigned to a
record, and this index does not invent one. Day 16 has no ADR and no changelog
entry.

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](0001-monorepo-and-stack.md) | Monorepo and stack | Accepted for the initial architecture; implementation deferred | 2026-10-09 |
| [0002](0002-backend-skeleton-conventions.md) | Backend skeleton: configuration, logging and error conventions | Accepted | 2026-10-09 |
| [0003](0003-data-layer-models-migrations-and-encryption.md) | Data layer: models, migrations, encryption at rest and repositories | Accepted | 2026-10-09 |
| [0004](0004-authentication-and-consent.md) | Authentication, anonymous mode and consent | Accepted | 2026-10-09 |
| [0005](0005-frontend-skeleton.md) | Frontend skeleton: tokens, API client and the accessibility floor | Accepted | 2026-10-09 |
| [0006](0006-emotion-model.md) | Emotion model: choice, mapping and licence | Accepted | 2026-10-09 |
| [0007](0007-ci-and-docker.md) | CI pipeline, Docker packaging and the containerised dev stack | Accepted | 2026-10-09 |
| [0008](0008-crisis-detection-rules-engine.md) | Crisis detection: a deterministic rules engine ahead of every model call | Accepted | 2026-10-10 |
| [0009](0009-safety-ml-ensemble.md) | A one-way ML backstop next to the rules engine | Accepted | 2026-10-10 |
| [0010](0010-llm-abstraction.md) | The LLM provider layer: one interface, a chain that cannot fail | Accepted | 2026-10-10 |
| [0011](0011-chat-orchestrator.md) | The chat orchestrator: one pipeline, and the order is the safety property | Accepted | 2026-10-10 |
| [0012](0012-chat-ui.md) | The chat UI: reading a stream and staying calm while doing it | Accepted | 2026-10-10 |
| [0013](0013-knowledge-retrieval-rag.md) | Knowledge base and retrieval (RAG) | Accepted | 2026-10-11 |
| [0014](0014-output-guard-and-conversation-memory.md) | Deterministic output guard and saved-chat memory | Accepted | 2026-10-11 |
| [0015](0015-mood-check-ins.md) | Mood check-ins | Accepted | 2026-10-11 |
| — | *(no record; see the gap above)* | — | — |
| [0017](0017-encrypted-journal-and-envelope-encryption.md) | Encrypted journal, AES-256-GCM envelope encryption and client-side search | Accepted | 2026-10-11 |
| [0018](0018-guided-exercises.md) | Declarative guided exercises and a local, accessible runner | Accepted | 2026-10-11 |
| [0019](0019-cbt-thought-record.md) | Seven-step CBT thought record | Accepted | 2026-10-11 |
| [0020](0020-phq-9-and-gad-7-screenings.md) | PHQ-9 and GAD-7 screenings: the official instrument, help before the score | Accepted | 2026-10-11 |
| [0021](0021-daily-plan-and-week-3-review.md) | Daily plan: a transparent rule table, not a recommender | Accepted | 2026-10-12 |
| [0022](0022-privacy-controls-and-data-export.md) | Privacy controls: export everything, delete everything | Accepted | 2026-10-12 |
| [0023](0023-multilingual-english-hindi-bengali.md) | Multilingual: English, Hindi and Bengali, in order of trust | Accepted | 2026-10-12 |
| [0024](0024-evaluation-harness.md) | Evaluation harness, bootstrap CIs and dual-layer safety gates | Accepted | 2026-10-12 |
| [0025](0025-pwa-accessibility-low-bandwidth.md) | Offline help, accessibility to WCAG 2.2 AA and a budget for low-end phones | Accepted | 2026-10-12 |
| [0026](0026-deployment-platform.md) | Deployment platform: Fly.io for the API and Postgres, Netlify for the web | Accepted | 2026-10-13 |
| [0027](0027-generated-api-reference.md) | Generated API reference with a drift test | Accepted | 2026-10-13 |
| [0028](0028-model-card-and-clinical-review-gate.md) | Model card and clinical-review checklist as documentation gates | Accepted | 2026-10-13 |
| [0029](0029-branch-coverage-gates.md) | Per-group branch coverage gates | Accepted; met on Day 28 after two tests were added | 2026-10-13 |
| [0030](0030-red-team-suite-and-session-risk.md) | Red-team suite, session risk accumulator, stored-channel injection tripwire, family-bound access tokens, export budget | Accepted (Day 29) | 2026-10-13 |
| [0031](0031-ml-platform.md) | ML platform: data catalogue, DVC-versioned data, MLflow tracking, config-driven reproducible runs | Accepted (Day 31) | 2026-10-13 |
| [0032](0032-emotion-lora-finetune.md) | Fine-tuning the multilingual emotion model: LoRA on XLM-R, nine-label taxonomy, packaged artefact behind `EMOTION_ANALYZER=trained` | Accepted (Day 32) | 2026-10-13 |
| [0033](0033-safety-trained-classifier.md) | Training the safety classifier: cost-sensitive loss, temperature calibration, conformal abstention, Integrated-Gradients review, behind `SAFETY_ML=trained` | Accepted (Day 33) | 2026-10-14 |

Adding a record: copy the layout of [0026](0026-deployment-platform.md) (Status,
Date, Context, Decisions, Consequences, Verification). Take the next free number
and do not reuse a retired one.
