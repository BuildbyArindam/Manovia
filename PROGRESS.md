# Manovia — Progress

## Current status
Day 1 repository scaffold and agent memory are in progress. No application code or dependencies are being added today. The checkout currently contains only the root README; the referenced `chatbot-1/` source is not present.

## Completed
- Initial repository inspection: only `README.md` is tracked at the starting commit.
- Confirmed there is no `chatbot-1/` directory or attached 49-day plan file available in the workspace.
- Day 1 structure and tooling documentation are being scaffolded.

## Decisions (ADR index)
- [0001 — Monorepo and stack](docs/adr/0001-monorepo-and-stack.md): record the selected API, persistence, frontend, LLM-provider abstraction, and containerization stack; implementation is deferred.

## Known issues
- The legacy `chatbot-1/` source is absent from this checkout, so it cannot be moved with `git mv` or verified for history preservation.
- `Manovia_49_Day_Plan.md` is not available in the workspace; no plan file can be copied into `docs/plan/`.
- Day 1 tooling targets are placeholders. No FastAPI/React application or tests exist yet.
- `pre-commit` and `ruff` are not installed in the sandbox; installing tools is out of scope for Day 1.

## Parking lot
- Restore the original legacy source/history when it is supplied; preserve it unchanged under `legacy/chatbot-1/`.
- Implement backend, frontend, evaluations, CI workflows, and application behavior in their scheduled days, with offline fakes and tests.
- Add the 49-day plan unchanged if it becomes available.

## Next steps
1. Recover the original `chatbot-1/` source and move it with `git mv` without editing its contents.
2. Scaffold the FastAPI backend settings and health endpoint with tests and fake external providers.
3. Scaffold the React + Vite + TypeScript frontend with accessible baseline UI and tests.
