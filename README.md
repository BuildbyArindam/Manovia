# Manovia

A privacy-first, safety-first mental wellbeing self-help companion—not therapy or diagnosis.

> **Important:** Manovia is not a substitute for professional care. If you are in crisis, call your local emergency number now.

[![CI](https://github.com/BuildbyArindam/Manovia/actions/workflows/ci.yml/badge.svg)](https://github.com/BuildbyArindam/Manovia/actions/workflows/ci.yml)
![Backend coverage](https://img.shields.io/badge/backend%20coverage-100%25-brightgreen.svg)

## Features

- **Supportive chat** — a safety-first chat surface. Deterministic crisis rules run *before* any model call; high-risk messages get a pre-written support reply and helpline resources, never a generation. Everything else flows through PII redaction, curated retrieval, a provider chain (Anthropic / Ollama / canned local fallback), and a whole-reply output guard before anything is persisted or shown ([ADR 0011](docs/adr/0011-chat-orchestrator.md)).
- **Safety evaluation** — rules-first + ML crisis detection with an ensemble, an offline eval set, and `evals/run_safety_eval.py` scoring the pipeline against labelled prompts.
- **Mood check-ins** — 5-face valence and energy scales, emotion and factor tags, timezone-correct history and streaks, encrypted notes ([ADR 0015](docs/adr/0015-mood-check-ins.md)).
- **Encrypted journal** — AES-256-GCM envelope-encrypted entries with per-entry keys ([ADR 0017](docs/adr/0017-encrypted-journal-and-envelope-encryption.md)).
- **Guided exercises** — eight validated, audio-free exercises with a keyboard-operable local runner, pause/resume, and reduced-motion support ([ADR 0018](docs/adr/0018-guided-exercises.md)).
- **CBT thought record** — a seven-step, owner-only encrypted wizard with per-step safety screening and a closed-set thinking-trap helper ([ADR 0019](docs/adr/0019-cbt-thought-record.md)).
- **PHQ-9 & GAD-7 screenings** — the official instrument wording and published scoring, with support options shown before any score when PHQ-9 item 9 is positive ([ADR 0020](docs/adr/0020-phq-9-and-gad-7-screenings.md)).
- **Daily plan** — a transparent, rule-based planner that suggests at most three things a day, each with a "why this?" explanation, honours quiet hours, and reduces to support-only after a positive item 9 ([ADR 0021](docs/adr/0021-daily-plan-and-week-3-review.md)).
- **Works offline, for everyone** — an installable app (PWA) in which "Need help now?" and the breathing exercises work with no signal; check-ins made offline wait on the device and are sent once you're back online with Manovia open; unsaved journal words survive a dropped connection. Built to WCAG 2.2 AA — keyboard-only use, screen-reader announcements, reflow at 200 % and 400 % zoom, 44 px targets, reduced motion and a high-contrast mode — with an in-app accessibility statement (`/accessibility`), and kept light for low-end phones on slow networks by bundle and Lighthouse budgets that fail CI ([ADR 0025](docs/adr/0025-pwa-accessibility-low-bandwidth.md)).
- **Foundations** — guest auth with consent gating, structured logging with log redaction, security headers, field-level encryption, and a React frontend with design tokens, two navigations, an onboarding gate, and a crisis helpline dialog.

Manovia is a self-help companion, not therapy or diagnosis. See [docs/architecture.md](docs/architecture.md) for how a message travels, and [docs/model-card.md](docs/model-card.md) for the safety model's intended use and limits.

## Screenshots

Captured from the production build in Chromium with a fresh guest account and
synthetic content only (Day 25). Regenerate with the command in
[docs/assets/README.md](docs/assets/README.md).

| Home on a 360 × 640 phone | "Need help now?" with no connection | A check-in saved offline |
| --- | --- | --- |
| <img src="docs/assets/day25-home-mobile-360x640.png" width="240" alt="Manovia home screen on a small phone: a greeting, then a quick check-in with five face buttons from Very low to Very good, and the bottom navigation."> | <img src="docs/assets/day25-offline-help-mobile-360x640.png" width="240" alt="The Need help now dialog while offline: a note says these are the helplines saved on this device the last time you were online, followed by the first helpline entry."> | <img src="docs/assets/day25-offline-checkin-mobile-360x640.png" width="240" alt="The confirmation after checking in offline: saved on this device, it will be sent by itself the next time Manovia is open and you're online; the note stays on this device until then; a Need help now button."> |

| Settings in high contrast | Exercises in dark high contrast | The accessibility statement |
| --- | --- | --- |
| <img src="docs/assets/day25-settings-high-contrast-desktop-1280x800.png" width="320" alt="Settings at desktop width with High contrast selected: palette and contrast choices as large radio cards, and a line saying text pairs are at least 7:1."> | <img src="docs/assets/day25-exercises-dark-high-contrast-desktop-1280x800.png" width="320" alt="The Exercises page in the dark high-contrast palette: a note that exercises work offline, the optional suggestions form, and exercise cards with large Try buttons."> | <img src="docs/assets/day25-accessibility-statement-desktop-1280x800.png" width="320" alt="The Accessibility page: what Manovia aims for (WCAG 2.2 AA and beyond) and a list of what it does, such as keyboard use, landmarks, labelled forms and 44 pixel targets."> |

The Day 12 chat screenshots (light, dark and the crisis card, at both sizes)
are in [docs/assets](docs/assets/README.md).

## Architecture

Manovia is a monorepo: a FastAPI backend, a React + Vite + TypeScript frontend, PostgreSQL persistence (SQLite for local work), and provider-abstracted language-model integrations. See [ADR 0001](docs/adr/0001-monorepo-and-stack.md), [ADR 0002](docs/adr/0002-backend-skeleton-conventions.md) and [ADR 0005](docs/adr/0005-frontend-skeleton.md).

## Quick start

### With Docker (the easy way)

Docker Desktop (or Docker Engine + the compose plugin) is the only prerequisite:

```bash
cp .env.example .env     # optional: the stack runs on dev-only defaults without it
make up                  # build and start api + web + postgres in the background
make smoke               # end-to-end smoke test: readiness, guest, consent, web 200
docker compose logs -f   # follow the logs (Ctrl-C to detach)
make down                # stop the stack (wipe the DB with: docker compose down -v)
```

Once `make up` reports the services started: web on <http://localhost:3000>,
API on <http://localhost:8000> (interactive docs at `/docs`). In development
the API container runs `alembic upgrade head` on every start, so a fresh stack
lands on a migrated database (see `backend/docker-entrypoint.sh`; it never
auto-migrates outside `APP_ENV=development`).

### Without Docker (local toolchain)

Python 3.11+ and Node 20.19+:

```bash
# Backend — API on http://localhost:8000 (interactive docs at /docs)
cd backend
pip install -e ".[dev]"   # or: uv sync
alembic upgrade head      # create the schema
make dev

# Frontend — http://localhost:5173, with /api proxied to the backend
cd ../frontend
npm ci
npm run dev
```

| Command                       | What it does                                        |
| ----------------------------- | --------------------------------------------------- |
| `make up`                     | Build and start the Docker dev stack                |
| `make down`                   | Stop the Docker dev stack (keeps the DB volume)     |
| `make smoke`                  | Run `scripts/smoke.sh` against the stack            |
| `make dev`                    | Backend API on :8000                                |
| `make frontend-dev`           | Frontend dev server on :5173 (proxies `/api`)       |
| `make test`                   | Backend pytest + frontend Vitest                    |
| `make lint`                   | Backend ruff + mypy, frontend ESLint                |
| `make format`                 | Backend ruff format, frontend Prettier              |
| `make db-upgrade`             | `alembic upgrade head`                              |
| `make seed`                   | Create the demo user and its rows                   |
| `make eval`                   | Run the Day 9 safety evaluation (`evals/run_safety_eval.py`) |

The frontend's own commands live in [frontend/README.md](frontend/README.md),
including the Day 25 production-build checks: `npm run budget` (bundle
budgets), `npm run test:e2e:prod` (axe, keyboard, zoom, offline and
installability in a real browser) and `npm run lighthouse` (mobile Lighthouse
with budgets). CI runs all three in `.github/workflows/a11y-lighthouse.yml`.

## Safety

Manovia is a self-help companion, not therapy, a diagnostic tool, or a crisis service. Crisis/self-harm detection must run before any LLM call; high-risk messages must receive deterministic, pre-written support. Every LLM response must pass an output-safety check. See [AGENTS.md](AGENTS.md) for the non-negotiable safety rules.

## Privacy

Configuration comes only from environment variables (see `.env.example`); secrets are never committed. Message text, journal entries and mood notes are encrypted at rest (Fernet, per-field) and the structured logger recursively drops raw user text (`message_text`, `content`, `body`, `note`, credentials) — verified by an end-to-end log-redaction test. Identifiers are never sent to LLM providers.

## Roadmap

- **Week 1** — repository scaffolding, backend skeleton, data layer, auth with consent, frontend skeleton, NLP service, CI and Docker stack.
- **Week 2** — crisis rules before any model call, the safety ML classifier and first eval set, the LLM provider layer with PII redaction, the chat orchestrator and chat UI, knowledge base and retrieval, output guard and encrypted conversation memory.
- **Week 3** — mood check-ins, encrypted journal, guided exercises, the CBT thought record, PHQ-9/GAD-7 screenings, and the daily plan with a Week-3 review.
- **Week 4 (in progress)** — privacy controls with export and real deletion, English/Hindi/Bengali, the evaluation harness, and offline support, accessibility and low-bandwidth performance (PWA).
- **Next** — deployment workflows, clinician and native-speaker review of crisis content and safe replies (Hindi/Bengali first), and continued hardening of the safety surfaces.
