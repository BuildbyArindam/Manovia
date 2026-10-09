# Manovia

A privacy-first, safety-first mental wellbeing self-help companion—not therapy or diagnosis.

> **Important:** Manovia is not a substitute for professional care. If you are in crisis, call your local emergency number now.

[![CI](https://github.com/BuildbyArindam/Manovia/actions/workflows/ci.yml/badge.svg)](https://github.com/BuildbyArindam/Manovia/actions/workflows/ci.yml)
![Backend coverage](https://img.shields.io/badge/backend%20coverage-100%25-brightgreen.svg)

## Features

Day 1 was repository scaffolding; Day 2 added the FastAPI backend skeleton (configuration, structured logging with log redaction, request IDs, security headers, a consistent error envelope, and health/readiness endpoints); Days 3–4 added the data layer and authentication; Day 5 added the React frontend skeleton (design tokens, the app shell with its two navigations, the onboarding gate, the crisis helpline dialog, and the typed API client). Product features — starting with the chat surface and the crisis rules that must run before any model call — arrive with their safety requirements and tests in later milestones.

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

The frontend's own commands live in [frontend/README.md](frontend/README.md).
Only the evaluation target (`make eval`) is still a placeholder.

## Safety

Manovia is a self-help companion, not therapy, a diagnostic tool, or a crisis service. Crisis/self-harm detection must run before any LLM call; high-risk messages must receive deterministic, pre-written support. Every LLM response must pass an output-safety check. See [AGENTS.md](AGENTS.md) for the non-negotiable safety rules.

## Privacy

Configuration comes only from environment variables (see `.env.example`); secrets are never committed. Message text, journal entries and mood notes are encrypted at rest (Fernet, per-field) and the structured logger recursively drops raw user text (`message_text`, `content`, `body`, `note`, credentials) — verified by an end-to-end log-redaction test. Identifiers are never sent to LLM providers.

## Roadmap

- Day 1: repository structure, tooling placeholders, and agent memory.
- Days 2–4: backend skeleton, data layer, and authentication with consent.
- Day 5: frontend skeleton — tokens, shell, onboarding, crisis dialog, API client.
- Day 6: NLP service — emotion and sentiment analysis behind one degradable interface.
- Day 7: CI (backend/frontend/secrets/audit jobs), Docker images and compose stack, smoke test.
- Next: the chat surface, behind the deterministic crisis rules that must run before any model call.
- Later: evaluation tooling, deployment workflows, and the remaining product surfaces.
