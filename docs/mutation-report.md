# Safety mutation report (Day 26)

Generated with `backend/scripts/mutation_safety.py` on 2026-10-24. A mutant is one
deliberate small mistake (a flipped comparison, `and` swapped for `or`, a dropped
`not`, a number moved by one, `max` turned into `min`). A mutant is **killed** when
a guarding test fails and **survives** when none does. A survivor is a gap in the
tests, not in the code under test.

## Before and after

| Module | Mutants | Killed before | Killed after | Survivors after |
|---|---|---|---|---|
| `safety/ensemble.py` | 32 | 16 (50.0%) | 32 (100%) | 0 |
| `safety/escalation.py` | 35 | 26 (74.3%) | 35 (100%) | 0 |
| `safety/session_risk.py` | 27 | 22 (81.5%) | 27 (100%) | 0 |
| `safety/rules.py` (40 of its mutation sites, evenly sampled) | 40 | 26 (65.0%) | 39 (97.5%) | 1 |

The first three modules were run exhaustively: 94 mutants, 64 killed before
(68.1%), 94 killed after. `rules.py` is 656 lines, so only an even sample was
run; its remaining sites have **not** been mutated and may hide further gaps.

## What the surviving mutants were, and what killed them

- **Thresholds one step off** (`>=` vs `>` at the confidence floor, the crisis-mass
  floor and the suspicion floor in the ensemble). The "never lowers" property test
  cannot see these: a moved threshold still never lowers a level. It changes who
  sees a crisis card. `tests/safety/test_ensemble_boundaries.py` pins each edge to
  an exact value.
- **Audit fields** (`ml_used`, `uncertain_checkin`, `raised_by_ml`) that nothing
  asserted. These feed the metadata-only safety event, so a wrong value is a wrong
  audit row.
- **Session accumulation numbers** (window of 8, points per tier, the neutral
  default of 0, "raise to the level it already has is not a raise"), in
  `tests/safety/test_escalation_session_boundaries.py`.
- **Long-message reading** in `rules.py`: the 256-character overlap, the 64,000
  character cap (exact, both sides), the first character of a long message, a
  window smaller than the overlap, and quotation handling for messages with no risky
  wording or only punctuation inside quotes, in `tests/safety/test_rules_windowing.py`.

## Remaining survivor, reviewed and left

| Mutant | Triage |
|---|---|
| `rules.py` L219 `>=` to `>` in `_assess_windowed` | Treated as equivalent. With `>` one extra window is read, and it is a suffix of the previous window. It cannot contain wording the previous window did not, though a shorter window could in principle lose a negation cue and read differently. No test distinguishes the two, and none was written. |

## Limits

- Only the decision code was mutated, not the pattern data
  (`content/safety/patterns_*.yaml`), which the 120+ table cases cover instead.
- The operator set is small (comparisons, boolean logic, constants, `max`/`min`).
  It does not delete statements or change return values.
- Some mutants are equivalent (dataclass defaults that every caller overrides).
  Those were killed by pinning the documented default, which is a small test of
  limited value; they are counted as killed.
- The runner mutates the file in place and restores it, so run it only when nothing
  else is editing or testing the same files.

Reproduce: `cd backend && python scripts/mutation_safety.py --report ../docs/mutation-report.md`
(about 10 minutes for the three exhaustive modules, plus `--target rules --limit 40`).
