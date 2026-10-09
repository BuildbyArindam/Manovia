# Manovia — Progress

## Current status
Day 1 repository scaffold and agent memory are complete. No application code or dependencies have been added. This checkout started with only the root `README.md` and has no legacy Flask chatbot source. Work is on the session branch `arena/200c0adc-manovia`; the requested `day-01-repo-restructure-tooling-agent` branch was not used.

## Completed
- Added the requested backend, frontend, docs, evaluations, scripts, and GitHub-workflow scaffolding, with placeholder READMEs for empty folders.
- Added the root README skeleton, MIT license placeholder, environment template, ignore rules, Makefile stubs, and pre-commit configuration.
- Added the exact supplied Agent Operating Rules text in `AGENTS.md` and recorded the initial stack decision in [ADR 0001](docs/adr/0001-monorepo-and-stack.md).
- Added `docs/plan/README.md` as a placeholder; the 49-day plan itself was not available in the workspace.
- Pushed the first Day 1 milestone as commit `1161452` (`chore: scaffold day-one repository foundation`).
- Verification results:
  - `make dev && make test && make lint && make format && make eval && make up && make down` exited 0; each of the seven targets printed `not implemented yet`. These are target-existence checks, not functional tests or lint runs.
  - Final `make test && make lint` rerun after the progress update also exited 0; both targets remain placeholders.
  - `grep -c "" AGENTS.md PROGRESS.md README.md LICENSE .env.example` reported 35, 28, 34, 21, and 24 lines respectively.
  - The six numbered non-negotiable safety rules were counted in `AGENTS.md`.
  - All nine required `.env.example` keys are present and empty; no non-empty assignments were found.
  - `find . -maxdepth 2 -not -path './.git*' | sort` listed the visible planned structure. Its `./.git*` filter also hides `.gitignore` and `.github`; both `.gitignore` and `.github/workflows/README.md` were verified separately.
  - The requested `git log --follow` probe for `legacy/chatbot-1/README.md` returned no entries because that legacy path does not exist. The root README's history is present, but it is not legacy history.
  - `pre-commit run --all-files` was skipped because `pre-commit` is not installed. No tools or application dependencies were installed, as required.

## Decisions (ADR index)
- [0001 — Monorepo and stack](docs/adr/0001-monorepo-and-stack.md): FastAPI + Pydantic v2, SQLAlchemy 2 + Alembic + PostgreSQL, React + Vite + TypeScript + Tailwind, provider-abstracted LLM integrations with offline fakes, and Docker for local orchestration. Implementation is deferred.

## Known issues
- `chatbot-1/` is absent from the checkout and initial Git tree, so the requested `git mv` and legacy-history verification could not be performed. No legacy code was fabricated or edited.
- `Manovia_49_Day_Plan.md` was not available at `/home/user/uploads/`; it could not be copied unchanged into `docs/plan/`.
- All Makefile targets are deliberate Day 1 placeholders. There is no backend/frontend application or functional test suite yet.
- `pre-commit` and `ruff` are not installed. Pre-commit hooks were not run; installation is out of scope for Day 1.

## Parking lot
- Restore the original legacy source/history when supplied, then preserve it unchanged under `legacy/chatbot-1/` with `git mv`.
- Add the 49-day plan unchanged when the file is available.
- Implement the backend, frontend, evaluations, CI workflows, and safety/privacy controls in their scheduled milestones, each with offline fakes and tests.

## Next steps
1. Recover the original `chatbot-1/` source and move it with `git mv` without editing its contents.
2. Scaffold FastAPI settings and a health endpoint with unit/integration tests and fake external providers.
3. Scaffold the React + Vite + TypeScript frontend with an accessible baseline UI and tests.
