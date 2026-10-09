# ADR 0001: Monorepo and stack

- **Status:** Accepted for the initial architecture; implementation deferred
- **Date:** 2026-10-09

## Context

Manovia is a privacy-first, safety-first mental wellbeing self-help companion. The application is not therapy, a diagnostic tool, or a crisis service. Its backend, frontend, evaluations, and operational documentation should evolve together, with safety checks and tests treated as release requirements.

## Decisions

1. **Repository layout:** Use a monorepo with `backend/`, `frontend/`, `docs/`, `evals/`, and `scripts/`. Keep any recovered Flask chatbot unchanged under `legacy/chatbot-1/`.
2. **Backend:** Use Python 3.11+, FastAPI, and Pydantic v2 for a typed HTTP API.
3. **Persistence:** Use SQLAlchemy 2 with Alembic migrations and PostgreSQL as the relational database.
4. **Frontend:** Use React, Vite, TypeScript, and Tailwind CSS.
5. **LLM integration:** Put providers behind a provider-abstracted interface configured through environment variables. Provide fake providers for tests so the suite can run offline. Run crisis/self-harm detection before any provider call and validate all model output before it is returned.
6. **Local orchestration:** Use Docker for local development and service orchestration once the application services exist.

## Consequences

- Backend and frontend changes can be reviewed and tested together.
- Provider fakes make unit and integration tests deterministic and offline-capable.
- PostgreSQL migrations and Docker-based local services must be introduced with tested application milestones.
- This ADR records intended architecture only; no application code, dependencies, or containers are created on Day 1.
