# Day 37 verification — contextual-bandit intervention policy

**Run date:** 2026-10-15 (UTC)  
**Scope:** synthetic, offline research only. The service seam is not wired into `GET /plan/today`; the deterministic daily-plan behavior is unchanged. No model weights or event-level datasets are committed.

## Reproduction and measured simulation

Run from the repository root after installing `backend[dev]` and `ml[ml,dev]`:

```bash
make ml-jitai-smoke
make ml-jitai-sim
```

Both commands completed successfully and logged aggregate metrics to local MLflow.

- **Smoke:** 20 seeds, 4 synthetic users × 10 decisions per online policy; 4,800 in-memory interactions; SHA-256 `4a9805ac5d3ddc65d3def86dc7ad60b8089925f902ccf7e63c3c0dbcf3904ff8`; MLflow run `016f2f7ea48c44a6ae013a7528fab5b0`; report written to `/tmp/manovia-day37-jitai-smoke.md`. The short episodes are a plumbing check; their noisy OPE/fairness values are not decision evidence.
- **Full evaluation:** 20 independent seeds, 12 synthetic users × 60 decisions per online policy; 108,800 in-memory interactions; SHA-256 `88b0e9af393502abf7d7327e4ed6924b4f8a31dd4326a09873e76f63ebf4ffc7`; MLflow run `f329789262f64a4ab624e0e72dd1ca42`; aggregate report `ml/reports/day37-jitai.md` and summary in `docs/evaluation-report.md`.
- Cumulative reward/regret confidence intervals are normal-approximation 95% intervals across the 20 seed-level results. The random baseline is an unconstrained offline-only reference; epsilon-greedy, LinUCB and Thompson sampling use the safety wrapper.
- Full-run online means (reward; regret; early → late regret per decision): random `-73.388` (`[-76.016, -70.759]`); `354.908` (`[352.823, 356.994]`); `0.4830 → 0.5004`. Epsilon-greedy `168.710` (`[157.803, 179.617]`); `115.455` (`[103.145, 127.765]`); `0.3390 → 0.0713`. LinUCB `193.795` (`[186.693, 200.897]`); `84.867` (`[78.846, 90.888]`); `0.2374 → 0.0670`. Thompson sampling `155.293` (`[147.573, 163.012]`); `125.985` (`[117.440, 134.530]`); `0.3390 → 0.1002`. Reward and regret CIs are in the linked report.
- Held-out logged uniform-behavior OPE against the simulator's expected target-policy value `0.3854`: IPS `0.3917` (95% CI `[0.3741, 0.4094]`, absolute gap `0.0063`); DR `0.3900` (`[0.3827, 0.3972]`, gap `0.0046`). These are plumbing checks for the synthetic, one-step, overlapping setup only.
- Four-group fairness gaps were `0.018` random, `0.130` epsilon-greedy, `0.106` LinUCB, and `0.113` Thompson sampling. The configured review threshold is `0.15`; all full-run rows were `OK`. The tiny smoke run intentionally has noisier values and two `FLAG`s.
- The full report includes five feature-derived explanation lines for human wording review. They describe associations, not causal effects, and preserve the option to choose another suggestion or skip. The tiny smoke may use baseline-only wording because its posteriors have almost no data.

## Safety and reward verification

Named backend tests in `backend/tests/unit/test_plan_policy_constraints.py` cover the MEDIUM+ risk gate (`test_medium_or_higher_risk_never_calls_policy`), item-9 gate (`test_phq9_item_9_response_never_calls_policy`), property-based gate ordering (`test_property_risk_gate_precedes_any_policy_call`), rolling exploration cap and no-consecutive exploration (`test_exploration_cap_is_at_most_twenty_percent_and_never_consecutive`, `test_property_exploration_cap_under_arbitrary_proposals`), and the curated allow-list (`test_only_curated_actions_are_ever_returned`). Tests also cover propensity logging without user key/context/text and user choice/skip. The reward module and feature encoder accept no free-text input.

**Mutation check (run, then reverted):** temporarily changed the outer gate from `if risk_level >= RiskLevel.MEDIUM:` to `if False and risk_level >= RiskLevel.MEDIUM:`. Pytest exited `1`: both `test_medium_or_higher_risk_never_calls_policy` and `test_property_risk_gate_precedes_any_policy_call` failed. The property failure shrank to `risk=MEDIUM, item9=False` and showed the learner returned `body-scan` instead of `support-options`. The original guard was restored, and the targeted policy suite passed afterward.

**Reward-term grep:**

```bash
grep -En 'time_in_app|session_duration|streaks|engagement_score|session_length|return_frequency|notification_open' backend/app/services/plan/policy/reward.py
```

Result: no matches (expected). The reward is the bounded normalized pre/post self-rating delta plus the small started-abandonment penalty; a skip produces no update.

## Test and lint outputs

| Area | Command | Result |
| --- | --- | --- |
| Targeted backend policy tests | `pytest tests/unit/test_plan_policy_constraints.py tests/unit/test_plan_policy_bandits.py tests/unit/test_plan_policy_reward.py tests/integration/test_plan_policy_service.py -q` | **27 passed** (0.68s) |
| Full backend Ruff/format/mypy + frontend ESLint | `make lint` | **Passed** — Ruff clean; 396 backend files already formatted; mypy clean on 334 source files; ESLint clean |
| Full ML lint | `make ml-lint` | **Passed** — Ruff clean; 152 files formatted; mypy clean on 123 source files |
| Frontend type-check | `npm run typecheck` | **Passed** — application and service-worker TypeScript checks |
| Full ML tests | `make ml-test` | **473 passed, 11 skipped** (21.49s); skips are optional ONNX/training/PyTorch paths |
| Full frontend tests | `npm run test:run` | **613 passed, 3 skipped** across 74 passing files; one file skipped (89.73s) |
| Full backend tests | `make test` | **4,566 passed, 4 skipped** (501.03s; 49 warnings); the pytest portion passed, then the repository coverage gate returned non-zero |

The backend coverage gate failure is outside this task's changed files: overall coverage passed at 94.59% statements / 86.95% branches (85% floor), but the pre-existing `app/services/safety` group floor is 95% and the unchanged `ml_classifier.py` (87.39% statements / 77.78% branches) and `trained.py` (91.29% / 82.35%) remain below it. `make test` therefore exits `1` after the successful pytest run. No unrelated safety-classifier changes were made.

Additional environment checks: `npm ci` installed the existing lockfile (743 packages) and reported 9 audit findings (4 moderate, 5 high); no frontend dependency files changed. An optional `npm run format:check` reports pre-existing formatting drift in `frontend/e2e/day25/keyboard.spec.ts` and `frontend/src/scripts/playwrightFailures.test.ts`; neither file is part of this task and neither was reformatted. The suite also emits existing React `act(...)` and jsdom `window.scrollTo` warnings despite passing.

## Research boundary and next checks

- No serving integration, durable per-user posterior, real-user data, GPU training, trained weights, or clinical validation exists in this milestone.
- The target remains an offline research service seam. Before any product path is proposed, require a separate ADR, durable privacy-reviewed state, opt-in/override/rating semantics, clinical and native-language copy review, and a rollback plan.
- ADR: [`0037-personalised-intervention-policy.md`](../adr/0037-personalised-intervention-policy.md). Aggregate metrics and fairness table: [`evaluation-report.md`](../evaluation-report.md). ML implementation notes and reproducible commands: [`ml/README.md`](../../ml/README.md#day-37-contextual-bandit-intervention-policy-offline-research-only).
