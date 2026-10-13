# ADR 0029: Per-group branch coverage gates

- Status: accepted (Day 26 floors). **The safety floor is not met in the Day 28 run.**
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

## Status and consequences

- **Day 28 result:** the overall floor passes (statements 96.55%, branches 89.87%).
  The safety group fails at 91.67% against 95%. The misses are the branch from line 253 to 258 of
  `ensemble.py`, the case where `decide()` receives a disabled classifier, which no
  test exercises, and line 480 of `patterns.py`, the loader's "not a mapping"
  error path. The Day 27 record's "coverage gate PASS" does not
  hold in the Day 28 run. The README badge now shows the measured values.
- **CI does not run the gate.** `.github/workflows/ci.yml` runs pytest with an 80%
  statement floor and no branch measurement. The gate is enforced only by
  `make test`, locally.
- Two ways to close the gap. The first is to add the two missing tests. The
  second is to lower the safety floor. This record does not choose between them.
  Lowering a safety floor needs an explicit decision from the maintainer, and the
  decision is open. The safety floor stays at 95% until that decision is made.
- The CI workflow should run the gate once the safety group passes. Until then, the
  README says it is failing.

## Alternatives considered

- **Statement coverage only.** Rejected: it is blind to the untested branch that
  this record describes.
- **Per-line coverage on crisis modules alone.** Not chosen: it would need a new
  tool and would not cover the safety group as a whole.

## Verification

- `python scripts/coverage_gate.py /home/user/cov-final.json` (Day 28 run) prints
  the per-group results and exits 1 on the safety group.
- The two uncovered locations are named above and in PROGRESS known issues.
