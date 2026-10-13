## What changed

<!-- One paragraph. Link the issue or ADR if there is one. -->

## Why

<!-- The problem this solves, or the decision this records (add an ADR under docs/adr/ for behaviour, storage or safety trade-offs). -->

## How it was verified

<!-- Paste the real command output summaries. Never describe a result you have not run. -->

- [ ] `make lint` (backend: ruff check, ruff format --check, mypy; frontend: ESLint)
- [ ] `make test` (backend pytest with coverage gates; frontend Vitest)
- [ ] `make eval`, if a safety, emotion or response behaviour changed
- [ ] `make db-check`, if a model or migration changed

## Safety, privacy and content checklist

- [ ] Crisis detection still runs before any model call (AGENTS.md rule 1), and no path lets a model answer HIGH or IMMINENT.
- [ ] No raw message text, journal text or identifiers are logged or sent to an LLM provider (rules 5).
- [ ] Helpline data changed only in `backend/app/content/helplines.json`, with `source_url` and `last_verified` updated (rule 6).
- [ ] No secrets, real personal data or real messages in code, fixtures, screenshots or logs.
- [ ] Any change to crisis copy, helpline data, screening content, exercise instructions or escalation policy is listed for clinical review in `docs/clinical-review.md`.
- [ ] New copy is warm, plain and short (no toxic positivity), and translated copy is marked unreviewed unless a named reviewer has signed it off.
- [ ] Tests were added or updated for the behaviour changed, and no test was weakened or deleted to make it pass.

## Documentation

- [ ] README, docs and `PROGRESS.md` are updated where this changes a claim, a number or a limit.
- [ ] `CHANGELOG.md` has an entry under *Unreleased* if users or operators will notice.
