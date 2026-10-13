# ADR 0029: Per-group branch coverage gates

- Status: accepted (Day 26 floors). **Met on Day 28** after two tests were added.
- Date: 2026-10-13 (recorded on Day 28; the floors date from Day 26)
- Deciders: Manovia maintainers
- Supersedes: none
- Related: [0007 CI and Docker](0007-ci-and-docker.md), [0024 evaluation harness](0024-evaluation-harness.md)

## Context

Statement coverage alone can pass while the branches that decide safety go
untested. A crisis rule that is never exercised on one of its branches can ship
unnoticed. Day 26 added branch measurement and per-group floors, implemented in
`backend/scripts/coverage_gate.py` and run by `make test`.

## Decision

- Branch coverage is measured with `pytest --cov-branch`.
- The floors are: overall backend branch coverage at least 85%; every module
  under `app/services/safety` at least 95%; the crypto and scoring modules at 100%.
- The gate is run by `make test` on the report that the same run writes.
- The floors are not lowered to make a run pass. A failing group is fixed with
  tests, unless the maintainer records a decision to change a floor.

## Status and consequences

- **Day 28, before the tests:** the overall floor passed (statements 96.55%,
  branches 89.87%). The safety group failed at 91.67% against 95%. The two gaps
  were the branch from line 253 to 258 of `ensemble.py`, where `decide()` receives
  a disabled classifier, and line 480 of `patterns.py`, the loader's "not a
  mapping" error path. The Day 26 and Day 27 "gate PASS" statements did not hold
  for that tree.
- **Day 28, after the tests:** two tests were added to
  `backend/tests/safety/test_loader_and_engine_branches.py`, one for each gap. The
  gate passes: overall statements 96.56%, branches 89.95%; every safety module
  at 100% statements and 100% branches.
- **CI does not run the gate yet.** `.github/workflows/ci.yml` runs pytest with an
  80% statement floor and no branch measurement. The gate is enforced only by
  `make test`, locally. Adding `coverage_gate.py` to CI is the next step.

## Alternatives considered

- **Statement coverage only.** Rejected: it is blind to the untested branch that
  this record describes.
- **Per-line coverage on crisis modules alone.** Not chosen: it would need a new
  tool and would not cover the safety group as a whole.

## Verification

- Before the tests: the Day 28 run of `coverage_gate.py` exited 1 on the safety
  group. After the tests: the same command exits 0 with all rules satisfied.
- The full backend run and its gate output are recorded in the Day 28 verification
  note.
