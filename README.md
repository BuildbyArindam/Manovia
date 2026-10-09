# Manovia

A privacy-first, safety-first mental wellbeing self-help companion—not therapy or diagnosis.

> **Important:** Manovia is not a substitute for professional care. If you are in crisis, call your local emergency number now.

[![CI](https://img.shields.io/badge/CI-pending-lightgrey.svg)](#)
[![Coverage](https://img.shields.io/badge/coverage-pending-lightgrey.svg)](#)

## Features

Day 1 was repository scaffolding; Day 2 added the FastAPI backend skeleton (configuration, structured logging with log redaction, request IDs, security headers, a consistent error envelope, and health/readiness endpoints). Product features will be added with safety requirements and tests in later milestones.

## Architecture

Manovia is planned as a monorepo with a FastAPI backend, a React + Vite + TypeScript frontend, PostgreSQL persistence, and provider-abstracted language-model integrations. See [ADR 0001](docs/adr/0001-monorepo-and-stack.md) and [ADR 0002](docs/adr/0002-backend-skeleton-conventions.md).

## Quick start

Backend commands are wired up (Day 2). From the repository root:

```bash
cd backend
pip install -e ".[dev]"   # or: uv sync
make dev                 # API on http://localhost:8000 (interactive docs at /docs)
make test                # pytest with a coverage summary
make lint                # ruff + mypy
```

The root `make dev`, `make test`, `make lint`, and `make format` targets delegate to `backend/`. The frontend, evaluations, and Docker targets (`make eval`, `make up`, `make down`) are still placeholders.

## Safety

Manovia is a self-help companion, not therapy, a diagnostic tool, or a crisis service. Crisis/self-harm detection must run before any LLM call; high-risk messages must receive deterministic, pre-written support. Every LLM response must pass an output-safety check. See [AGENTS.md](AGENTS.md) for the non-negotiable safety rules.

## Privacy

The design requires environment-based configuration, no raw message text in INFO-level logs, and no identifiers sent to LLM providers. These controls are architectural requirements and are not implemented in this scaffold.

## Roadmap

- Day 1: repository structure, tooling placeholders, and agent memory.
- Next: backend foundations and tested provider interfaces.
- Later: accessible frontend, evaluation tooling, and deployment workflows.
