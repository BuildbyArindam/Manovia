# Day 38 verification — privacy-preserving ML

Verified on 2026-10-15. Arena-pinned branch `arena/e17d4e5d-manovia`, not `main`.
No branch switch, real records, GPU training or deployment. The requested branch
name cannot replace Arena's pinned session branch.

## Reproduced smoke evidence

Command (repository root):

```sh
PATH="$PWD/.venv/bin:$PATH" make ml-federated-sim
```

Exit **0**. MLflow run `a45a78eaa2d64ac5b90298a8a7fc4da9`; code commit
`1e7e9b7026ee737bf3234702998812206f4fe729`, clean tree at run start. The later
backend-test typing correction does not change training code. Config hash and
versions are in the [model card](../../ml/reports/day38-model-card.md).
50 clients; 655 training / 253 held-out pairs; 4 rounds, 2 local steps, seed 38.
The run records code state, hash, resolved config, seed, dependencies, metrics,
convergence and report/SVG in local ignored MLflow storage.

| Model | MASE ↓ | attack AUC | epsilon | delta | optimizer steps |
| --- | ---: | ---: | ---: | ---: | ---: |
| centralised | 1.3372 | 0.5518 | not DP | — | 8 |
| FedAvg IID drop=0.0 | 1.3380 | 0.5519 | not DP | — | local, see config |
| FedProx IID drop=0.0 | 1.3384 | 0.5518 | not DP | — | local, see config |
| FedAvg IID drop=0.3 | 1.3423 | 0.5527 | not DP | — | local, see config |
| FedProx IID drop=0.3 | 1.3427 | 0.5528 | not DP | — | local, see config |
| FedAvg non-IID drop=0.0 | 1.3381 | 0.5521 | not DP | — | local, see config |
| FedProx non-IID drop=0.0 | 1.3384 | 0.5520 | not DP | — | local, see config |
| FedAvg non-IID drop=0.3 | 1.3291 | 0.5516 | not DP | — | local, see config |
| FedProx non-IID drop=0.3 | 1.3295 | 0.5516 | not DP | — | local, see config |
| DP sigma=0.8 C=0.5 | 1.2635 | 0.5470 | 8.1267 | 1e-05 | 44 |
| DP sigma=0.8 C=1.0 | 1.2596 | 0.5484 | 8.1267 | 1e-05 | 44 |
| DP sigma=1.6 C=0.5 | 1.2761 | 0.5469 | 2.2014 | 1e-05 | 44 |
| DP sigma=1.6 C=1.0 | 1.2620 | 0.5477 | 2.2014 | 1e-05 | 44 |
| DP sigma=3.2 C=0.5 | 1.2707 | 0.5484 | 0.8417 | 1e-05 | 44 |
| DP sigma=3.2 C=1.0 | 1.2754 | 0.5459 | 0.8417 | 1e-05 | 44 |

No categorical accuracy applies to this regression task. The mean absolute
next-day/current-day difference on training pairs is the MASE denominator
(0.2588 normalised mood); held-out persistence MASE is **1.1150**, better than
every trained smoke head. No utility success is claimed. The central model uses
8 full-cohort steps; DP uses 44 Poisson steps, so DP's lower MASE is not a fair
compute-controlled benefit. IID/non-IID and dropout differences are small and
not consistent: FedProx does not improve this smoke; dropout sometimes helps
by chance. Participation and all per-round curves are in the generated report.

Predeclared point: sigma **3.2**, C **1.0**, epsilon **0.8417**, delta **1e-5**;
highest configured noise, permits more gradient signal than C=0.5, not selected
from holdout MASE. Its MASE difference from central is **-0.0618**. Epsilon is
unchanged by C when noise is expressed relative to C. Each sweep arm is a
separate hypothetical release. Assuming independent secret randomness, basic
composition of all six would be epsilon ≤22.3396, delta ≤6e-5, before any private
metrics, selection or repeated participation. Public-seeded research is not a
production privacy guarantee.

## Membership attack interpretation

Continuous score = **negative per-pair squared loss**, known candidate label;
ROC AUC, not an optimised test-set threshold or direction. Member/nonmember
samples are deterministically balanced to 253 each, drawn from the same
synthetic-person cohort with disjoint pairs/days. One weak attack, one seed,
no significance/robustness claim. The non-private AUC **0.5518** and chosen DP
AUC **0.5459** move only 0.0059 towards chance. **Empirical defence is NOT
established**; stronger predeclared attacks and confidence intervals remain
manual research work. The accountant is not validated by a favourable AUC.

## Real commands and tests

Use separate ML `.venv` and backend `backend/.venv`. Flower requires a uvicorn
version incompatible with the backend; neither pin was relaxed. Both environments
passed their own `pip check`. Initial commands failed when the environments were
mixed; separate installs resolved the dependency conflict. PyPI torch downloaded
CUDA-capable wheels, but all training tensors ran on **CPU**. An attempted wheel
upgrade exhausted disk cache; purging pip's download cache and reinstalling with
`--no-cache-dir` succeeded. No generated data or model files are tracked.

| Check | Real result | Verdict |
| --- | --- | --- |
| `make ml-federated-sim` | 15-arm table and 15-curve valid XML SVG reproduced; run above | PASS (smoke only) |
| Script entry point | `cd ml; PYTHONHASHSEED=0 ../.venv/bin/python notebooks/run_federated.py --config configs/federated_smoke.yaml --seed 38 --report-output ../.cache/day38-script-smoke.md` — all 15 metric rows exactly match make | PASS |
| `make ml-lint` | Ruff check/format clean, mypy: 129 source files | PASS |
| `make ml-test` | 546 passed, 1 skipped; 50.88s, 12 expected Opacus warnings | PASS |
| Real Flower weighted aggregation, joint clipping and RDP contracts | Included in full ML suite; 23 Day-38 tests including repeated integration | PASS |
| `make ml-catalogue` | 17 entries, generated table current, 10 restricted entries unused | PASS |
| `python -m manovia_ml.data.dvc_lock` (from ml environment) | Existing lock consistent; missing generated outputs expected | PASS |
| `PATH="$PWD/backend/.venv/bin:$PATH" make lint` | Ruff clean, 396 formatted files, mypy: 334 files; frontend ESLint clean | PASS |
| `npm --prefix frontend run typecheck` | Exit 0, both application and service-worker TypeScript | PASS |
| `npm --prefix frontend run test:run` | 613 passed, 3 skipped, 74 test files passed / 1 skipped (107.39s on the final root make test) | PASS |
| Full backend `make test` including coverage gate | **4,589 passed, 4 skipped**, 574.44s; all gate rules satisfied | PASS |
| Empirical defence against strong attacks | Not established by tiny loss-score AUC | NOT ESTABLISHED |
| Full five-seed synthetic training / phone controls | Prepared script/design, not executed or implemented | DEFERRED as requested |

The ML skip is a mutually exclusive no-torch fallback when torch is present;
all transformer and ONNX optional dependencies were installed for this full run.
Opacus warnings explicitly disclose `secure_mode=False` and backward-hook
behaviour; tests assert actual clipped sums and privacy-accountant values.
The final root `make test` completed backend, coverage gates and frontend with exit 0.
Frontend still emits existing React act/jsdom scrollTo warnings; no frontend
product or dependency file changed.

### Failures found and root-cause work (no weakened/deleted tests)

1. Day-36 user logging probability is jittered even when the cohort base is 1.
   Skip missing pair endpoints rather than assert every day exists. Regression
   verifies missingness, IID row conservation and no train/test raw-day overlap.
2. Generic shipped-config validation initially sent new YAMLs to `TrainConfig`.
   Register the new family with strict `FederatedConfig` validation. Unknown
   keys and invalid/empty sweeps or missing operating points still fail tests.
3. Full ML initially lacked ignored `data/safety_toy/` splits and optional
   checkpoint inspection dependencies. Generate toy splits using the existing
   offline builder and install the training extra; the full suite passes.
4. Review found DP arms initially reused a noise seed. Derive distinct streams
   per sigma/C; a regression captures the actual seeds passed to torch.
   Production composition still requires independent secret randomness.
5. First full backend run: **4,566 passed, 4 skipped**, but `make test` exited
   nonzero: safety `ml_classifier.py` 87.39% statements/77.78% branches and
   `trained.py` 91.29%/82.35%, below existing 95% floors (also recorded Day 37).
   Add 23 cases for corrupt metadata, invalid dimensions, local optional-loader
   boundaries and trained/ONNX fallback selection. Targeted classifier suite:
   **70 passed** after a test-typing correction. No safety production code,
   baseline weights, test exclusions or coverage thresholds changed.
   Final full run: **4,589 passed, 4 skipped**, all coverage rules satisfied;
   overall **94.83% statements / 87.48% branches**. `ml_classifier.py` now
   **100.00% / 97.22%**; `trained.py` **99.17% / 97.06%**. The original
   coverage failure is fixed by meaningful additional tests, not waived.

## Catalogue entries used

**Only §17 — Manovia synthetic federated mood pairs (Day 38)**:

- Source: repository Day-36 `mood_forecast/synthetic.py`, `state-space-v1`;
  repository Day-38 `federated/data.py` supplies pairing/splits.
- Licence: **MIT**, evidence `LICENSE`, verified **2026-10-15**.
- Allowed use: commercial; verdict **USE**; status used.
- Language: `zxx` (numeric); personal data: **false**; no external corpus.
- Smoke counts 655/253; SHA256
  `78a729e3f329137481e1c6436260411f33a12c9af70d92db5b8b875a8f9cd5cc`.
- Known biases: hand-authored dynamics, no clinical/demographic realism;
  within-person random holdout, not prospective/unseen-person performance.

[Full catalogue](../../ml/data/CATALOGUE.md#17-manovia-synthetic-federated-mood-pairs-day-38)
and [dataset card](../../ml/data/cards/day38-federated-mood.md).
No Day-32 external emotion corpus or real wellbeing records were used.

## Exhaustive design-claim audit

Read `docs/federated-design.md`; all 14 IDs are audited below. Proposed claims
are flagged, not silently treated as measurements.

| Claim | Audit |
| --- | --- |
| M1 — 50 synthetic non-IID clients, same-row IID control/disjoint holdout | Measured counts/hash, IID conservation and disjoint pair/day tests |
| M2 — Flower weighted FedAvg/FedProx and proximal local objective | Measured weighted toy, one-step equivalence and local-drift tests |
| M3 — independent dropout/heterogeneity/convergence | Measured curves/participant counts, all-dropout carry-forward test; adversarial dropout unmeasured |
| M4 — central record-level Opacus clipping/Poisson/RDP | Actual per-example clipping tests, monotonicity and independent-accountant step check; no user-level guarantee |
| M5 — loss-score AUC comparison | Measured single-seed weak attack; no demonstrated defence |
| D1 — on-phone head, local storage/inference and exclusions | **UNMEASURED / unimplemented**, phone benchmarks and storage review required |
| D2 — clipped-then-noised uploads only, minimum metadata, cryptographic noise | **UNMEASURED / unimplemented**, current FL is unnoised and separate central DP is seeded |
| D3 — secure aggregation, 30-of-50 minimum, abort/dropout recovery | **UNMEASURED / unimplemented**, audited protocol/collusion review required |
| D4 — separate default-off opt-in, opt-out/cancellation/key deletion and ledger | **UNMEASURED / unimplemented**, consent/deletion UX and tests required; no unlearning |
| D5 — charging/80%/Wi-Fi/thermal/2-minute/10-MB/daily rules | **UNMEASURED / unimplemented**, policy values only, OS/battery/network tests required |
| D6 — signed schema/model, admission, anomaly, rollback, authenticated anti-replay transport | **UNMEASURED / unimplemented**, poisoning, Sybil and transport validation required |
| L1 — no clinical/fairness/deployment/energy/secure-aggregation/user-level DP claim | Correct limit: supporting measurements absent |
| L2 — synthetic record-level epsilon not a production privacy budget | Correct limit: public-seeded research, multiple pairs/person, no production accountant |
| L3 — chance AUC not proof against inversion/linkage/attribute/stronger attacks | Correct limit: these attacks are not measured |

## Human follow-up (sandbox boundaries)

1. Run the ready-to-run script on Colab/Kaggle using the full config, seeds 38–42,
   collect every result and uncertainty across seeds; no favourable-seed picking.
2. Predeclare stronger membership attacks/intervals; independent privacy review
   of adjacency, secret randomness, user-level composition and private metric
   release before any real-data claim. Never upload user text/weights here.
3. No phone controls ship: separately implement/test consent cancellation,
   battery/network/storage rules and audited secure aggregation/collusion/dropout
   behaviour on real devices. Clinical/safety review before product integration.
4. Review GitHub CI after the PR opens. The workflow change is YAML-parsed and
   locally equivalent library paths are tested, but remote CI has not been run
   in this sandbox. The CPU wheel host is outside the sandbox allowlist.
