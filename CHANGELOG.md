# Changelog

All notable changes to Manovia are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Manovia has **no tagged
release yet**, so entries are grouped by build day (the sequence in
[PROGRESS.md](PROGRESS.md)) and link to the decision record and verification
note for each. When a release is tagged, the release workflow
(`.github/workflows/release.yml`) will be the place to cut a version.

## [Unreleased] — Day 28: documentation, model card and portfolio polish

### Added

- `docs/model-card.md` rewritten as a full model card: every model in the
  product (safety classifier, emotion model, language-model providers), intended
  and out-of-scope use, training and evaluation data, metrics, known failure
  modes, ethics and an update policy. The screening-instrument provenance is kept.
- `docs/clinical-review.md`: the checklist a licensed professional should
  complete before public launch (crisis copy, helplines, knowledge base,
  exercise instructions, screening presentation and escalation policy).
- `docs/api.md`, generated from the FastAPI OpenAPI schema by
  `scripts/export_openapi.py`; a test fails if the committed copy drifts.
- `docs/architecture.md`: sequence diagrams for the chat and crisis flows.
- ADRs for decisions that had none (see [docs/adr/README.md](docs/adr/README.md)).
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue forms and a pull
  request template under `.github/`.
- `docs/demo-script.md` (a 90-second demo) and demo assets under `docs/assets/`.

### Changed

- `README.md` rewritten: pitch, feature list, architecture and sequence views,
  safety and privacy summaries, evaluation headline numbers with a link to the
  report, quick start, tech stack, roadmap and honest limitations.
- `docs/safety-design.md`, `docs/ethics-and-limitations.md` finalised for launch
  review, with the open clinical questions stated plainly.

### Fixed

- The model card claimed OpenAI was a supported provider. It is not: the
  configuration accepts `anthropic`, `ollama` and `fake` only.
- The README's static "backend coverage 100%" badge did not match the measured
  coverage. It now quotes the measured value, from the Day 28 verification run.

## Day 27 — DevOps, observability and deployment

- Gunicorn process model with `UvicornWorker` processes; access log off for
  privacy; `GUNICORN_WORKERS=1` until the session store is shared.
- `docker-compose.prod.yml`: one-shot migration service; refuses to start
  without real `SECRET_KEY` and `FIELD_ENCRYPTION_KEY`.
- Prometheus metrics at `/metrics`, with closed-set labels only.
- Sentry with a privacy scrubber; opt-in by DSN.
- Feature flags; nightly backup and restore tooling, drilled against PostgreSQL 16.2.
- Fly.io (API and Postgres) and Netlify (web) deployment. See
  [ADR 0026](docs/adr/0026-deployment-platform.md) and
  [docs/deploy.md](docs/deploy.md).
- Verification: [docs/verification/day27-devops-observability-deployment.md](docs/verification/day27-devops-observability-deployment.md).

## Day 26 — Coverage gates

- Branch coverage measured; overall backend gate at 85%, every
  `app/services/safety` module at 95%, crypto and scoring at 100%
  (`backend/scripts/coverage_gate.py`, run by `make test`).

## Day 25 — Offline, accessibility and low bandwidth (PWA)

- Installable PWA; offline "Need help now?" and breathing exercises; offline
  check-in outbox; WCAG 2.2 AA work and an in-app accessibility statement;
  bundle and Lighthouse budgets.
- [ADR 0025](docs/adr/0025-pwa-accessibility-low-bandwidth.md) ·
  [verification](docs/verification/day25-pwa-accessibility-low-bandwidth.md).

## Day 24 — Evaluation harness

- `make eval`: safety, emotion and response suites with bootstrap confidence
  intervals and CI gates (`evals/thresholds.yaml`); reports under `evals/reports/`.
- [ADR 0024](docs/adr/0024-evaluation-harness.md) ·
  [evaluation report](docs/evaluation-report.md) ·
  [verification](docs/verification/day24-evaluation-harness.md).

## Day 23 — English, Hindi and Bengali

- Interface and crisis copy in English, Hindi and Bengali; language-filtered
  retrieval; translation status ledger ([translations/STATUS.md](translations/STATUS.md)).
- [ADR 0023](docs/adr/0023-multilingual-english-hindi-bengali.md) ·
  [verification](docs/verification/day23-multilingual-english-hindi-bengali.md).

## Day 22 — Privacy controls and security hardening

- Export everything, delete everything, retention settings, refresh-token
  rotation and lockout, OriginGuard for unsafe methods.
- [ADR 0022](docs/adr/0022-privacy-controls-and-data-export.md) ·
  [threat model](docs/privacy-and-threat-model.md) ·
  [verification](docs/verification/day22-privacy-controls-and-security-hardening.md).

## Day 21 — Daily plan and Week-3 review

- Rule-based daily plan with a "why this?" for each item; quiet hours; support-only
  mode after a positive PHQ-9 item 9.
- [ADR 0021](docs/adr/0021-daily-plan-and-week-3-review.md) ·
  [verification](docs/verification/day21-daily-plan-week-3-review.md).

## Day 20 — PHQ-9 and GAD-7 screenings

- The official instrument wording and published scoring; support shown before
  any score when PHQ-9 item 9 is positive.
- [ADR 0020](docs/adr/0020-phq-9-and-gad-7-screenings.md) · [model card](docs/model-card.md).

## Day 19 — CBT thought record

- Seven-step, owner-only, encrypted thought record with per-step safety screening.
- [ADR 0019](docs/adr/0019-cbt-thought-record.md) ·
  [verification](docs/verification/day19-cbt-thought-record.md).

## Day 18 — Guided exercises

- Eight declarative exercises with a keyboard-operable runner.
- [ADR 0018](docs/adr/0018-guided-exercises.md) ·
  [verification](docs/verification/day18-guided-exercises.md).

## Day 17 — Encrypted journal

- AES-256-GCM envelope encryption with per-entry keys; client-side search.
- [ADR 0017](docs/adr/0017-encrypted-journal-and-envelope-encryption.md).

## Day 15 — Mood check-ins

- Five-point valence and energy check-ins, tags, streaks, encrypted notes.
- [ADR 0015](docs/adr/0015-mood-check-ins.md).

## Day 14 — Output guard and saved-chat memory

- Deterministic whole-reply output guard with one strict regeneration; encrypted,
  user-controlled saved-chat memory.
- [ADR 0014](docs/adr/0014-output-guard-and-conversation-memory.md).

## Day 13 — Knowledge base and retrieval

- Curated self-help knowledge base; retrieval filtered by language.
- [ADR 0013](docs/adr/0013-knowledge-retrieval-rag.md).

## Day 12 — Chat interface

- Streaming chat UI, crisis card, light and dark themes.
- [ADR 0012](docs/adr/0012-chat-ui.md).

## Day 11 — Chat orchestrator

- One pipeline for every message; crisis gate before any model call.
- [ADR 0011](docs/adr/0011-chat-orchestrator.md).

## Day 10 — LLM provider layer

- One provider interface; a fallback chain that ends in a canned local reply.
- [ADR 0010](docs/adr/0010-llm-abstraction.md).

## Day 9 — Safety ML ensemble

- A one-way ML backstop next to the rules engine; `make eval` safety suite.
- [ADR 0009](docs/adr/0009-safety-ml-ensemble.md).

## Day 8 — Crisis rules engine

- Deterministic, pattern-based crisis detection with normalisation and pragmatics.
- [ADR 0008](docs/adr/0008-crisis-detection-rules-engine.md) ·
  [safety design](docs/safety-design.md).

## Days 1–7 — Foundations

- Monorepo, backend and frontend skeletons, data layer with Alembic, guest
  authentication with consent gating, the emotion model, CI and the Docker stack.
- ADRs [0001](docs/adr/0001-monorepo-and-stack.md) to
  [0007](docs/adr/0007-ci-and-docker.md).
