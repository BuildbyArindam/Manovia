# ADR 0001: Monorepo and stack

- **Status:** Accepted for the planned project architecture
- **Date:** 2026-10-09

## Context

Manovia is a privacy-first, safety-first mental wellbeing self-help companion. The repository needs an architecture that keeps safety policy and provider integrations testable, supports a web interface, and can be developed and evaluated offline. Day 1 is a scaffold only; no application dependencies or runtime code are being added.

## Decisions

1. **Monorepo:** Keep backend, frontend, documentation, evaluation material, and scripts in one repository.
2. **Backend:** Use Python 3.11+, FastAPI, and Pydantic v2 for the API and validation.
3. **Persistence:** Use SQLAlchemy 2 with Alembic for database access and schema migrations.
4. **Frontend:** Use React, Vite, TypeScript, and Tailwind CSS.
5. **LLM integration:** Place each LLM provider behind a provider abstraction. Use a deterministic Fake provider in tests so the suite can run fully offline. Safety and crisis checks must run before any model call.
6. **Deployment:** Use Docker for reproducible service packaging and local orchestration.

## Consequences

- The planned backend, frontend, and evaluation areas can evolve together while remaining separately testable.
- External providers must not be called directly from application policy code; test fakes and offline tests are required.
- Docker files, application code, dependencies, and CI are intentionally deferred from this Day 1 scaffold.
- These are stack decisions, not evidence that the corresponding services have been implemented.
