# Manovia Progress

## Current status
Day 1 repository scaffold is in progress on `arena/04f97eb5-manovia`. Verification is pending. The checkout contains only the initial `README.md`; there is no `chatbot-1/` source to migrate.

## Completed
- Added the project operating rules, repository scaffold, configuration templates, and documentation skeleton.
- Recorded the planned stack in [ADR 0001](docs/adr/0001-monorepo-and-stack.md).

## Decisions (ADR index)
- [0001 — Monorepo and stack](docs/adr/0001-monorepo-and-stack.md): use FastAPI, SQLAlchemy 2 + Alembic, React + Vite + TypeScript, a provider-abstracted LLM, and Docker.

## Known issues
- The supplied repository history and working tree contain only the initial `README.md`; no `chatbot-1/` files exist. A history-preserving `git mv` cannot be performed without the source. A placeholder marks the intended `legacy/chatbot-1/` location; the original source must be supplied before migration can be verified.
- Day 1 tooling targets are placeholders; there is no application code, test suite, or frontend/backend package to run yet.

## Parking lot
- Implement backend and frontend services after Day 1; keep each feature behind safety checks and covered by offline tests.
- Add CI, container definitions, and evaluation datasets/reports in their planned day.
- Confirm whether the original `chatbot-1/` source exists in another repository or branch and migrate it with Git history when available.

## Next steps
1. Verify the Day 1 structure, placeholder Make targets, configuration files, and Git history available in this checkout.
2. Record actual lint/test/tooling results and the missing-source limitation here.
3. Begin Day 2 only after the Day 1 scaffold is reviewed and the legacy source question is resolved or explicitly deferred.
