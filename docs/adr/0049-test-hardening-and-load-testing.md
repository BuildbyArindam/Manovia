# ADR 0049 — Test hardening: races, mutation testing, contract checks and load testing

Date: 2026-10-24. Status: accepted.

## Context

Day 26 asked for tests that fail when the code is wrong in ways a happy-path test
cannot see. The coverage gates (ADR 0029) were built then; the rest was not. This
ADR records the remainder and the decisions behind it.

## Decisions

1. **Two real concurrency bugs were fixed, not just tested.**
   - Refresh-token rotation read `revoked_at` and then wrote it. Two simultaneous
     refreshes of one token could both succeed, each minting a live successor. The
     claim is now one conditional `UPDATE ... WHERE revoked_at IS NULL`; the loser
     is treated as reuse and the family is revoked
     (`RefreshTokenRepository.claim_for_rotation`).
   - A double-tap or client retry stored the same questionnaire twice. Identical
     answers from one person for one instrument within 10 seconds are now one
     submission; a per-person lock serialises concurrent requests inside a worker.
     The item-9 support options are still returned on a duplicate.
   - Residual risk: across several workers the 10-second window relies on the
     database's read-committed view, not a lock, so two requests landing on
     different workers at the same instant could still both write. A unique
     idempotency key would close that and is parked.
2. **Mutation testing uses an in-repo runner, not mutmut.** mutmut copies the tree
   into `mutants/`; the safety suite reads `ml/` and `evals/` by relative path, so
   the copy fails at collection. `scripts/mutation_safety.py` mutates in place and
   always restores. It is a report, not a CI gate (a full run is about 10 minutes).
3. **API drift fails the build from the frontend side.** `openapi-typescript`
   generates `frontend/src/lib/api-schema.d.ts` from `docs/openapi.json`;
   `npm run api:check` fails when they differ; `src/lib/apiContract.ts` asserts, at
   compile time, that the wire types satisfy the hand-written client types. Both run
   in the existing frontend CI job. The contract is a small set of shapes, not the
   whole API; it grows by adding lines.
4. **Load testing is a repeatable script, not a one-off.** `backend/loadtest/`
   holds a Locust scenario and `run.sh`. Numbers are recorded with their
   environment because they only describe it.
5. **Flaky tests are hunted by repetition.** `scripts/flaky_hunt.sh` runs the suite
   N times and counts failures per test. A flaky test is fixed or quarantined with
   a note in PROGRESS.md, never retried.

## Consequences

- Locust is pinned in `backend/loadtest/requirements.txt`, not in `pyproject.toml`,
  so the runtime image and the dependency audit do not carry a load-test tool.
- The frontend gains one dev dependency (`openapi-typescript`, exact pin).
- The generated schema file is excluded from ESLint and Prettier.
