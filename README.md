# Manovia

A privacy-first, safety-first mental wellbeing self-help companion—not therapy or diagnosis.

> **Important:** Manovia is not a substitute for professional care. If you are in crisis, call your local emergency number now.

[![CI](https://img.shields.io/badge/CI-pending-lightgrey.svg)](#)
[![Coverage](https://img.shields.io/badge/coverage-pending-lightgrey.svg)](#)

## Features

Day 1 was repository scaffolding; Day 2 added the FastAPI backend skeleton (configuration, structured logging with log redaction, request IDs, security headers, a consistent error envelope, and health/readiness endpoints); Days 3–4 added the data layer and authentication; Day 5 added the React frontend skeleton (design tokens, the app shell with its two navigations, the onboarding gate, the crisis helpline dialog, and the typed API client). Product features — starting with the chat surface and the crisis rules that must run before any model call — arrive with their safety requirements and tests in later milestones.

## Architecture

Manovia is a monorepo: a FastAPI backend, a React + Vite + TypeScript frontend, PostgreSQL persistence (SQLite for local work), and provider-abstracted language-model integrations. See [ADR 0001](docs/adr/0001-monorepo-and-stack.md), [ADR 0002](docs/adr/0002-backend-skeleton-conventions.md) and [ADR 0005](docs/adr/0005-frontend-skeleton.md).

## Quick start

Both halves are wired up. From the repository root:

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
| `make dev`                    | Backend API on :8000                                |
| `make frontend-dev`           | Frontend dev server on :5173 (proxies `/api`)       |
| `make test`                   | Backend pytest + frontend Vitest                    |
| `make lint`                   | Backend ruff + mypy, frontend ESLint                |
| `make format`                 | Backend ruff format, frontend Prettier              |
| `make db-upgrade`             | `alembic upgrade head`                              |
| `make seed`                   | Create the demo user and its rows                   |

The frontend's own commands live in [frontend/README.md](frontend/README.md).
The evaluation and Docker targets (`make eval`, `make up`, `make down`) are still
placeholders.

## Safety

Manovia is a self-help companion, not therapy, a diagnostic tool, or a crisis service. Crisis/self-harm detection must run before any LLM call; high-risk messages must receive deterministic, pre-written support. Every LLM response must pass an output-safety check. See [AGENTS.md](AGENTS.md) for the non-negotiable safety rules.

## Privacy

The design requires environment-based configuration, no raw message text in INFO-level logs, and no identifiers sent to LLM providers. These controls are architectural requirements and are not implemented in this scaffold.

## Roadmap

- Day 1: repository structure, tooling placeholders, and agent memory.
- Days 2–4: backend skeleton, data layer, and authentication with consent.
- Day 5: frontend skeleton — tokens, shell, onboarding, crisis dialog, API client.
- Next: the chat surface, behind the deterministic crisis rules that must run before any model call.
- Later: evaluation tooling, deployment workflows, and the remaining product surfaces.
