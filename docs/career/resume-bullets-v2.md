# Resume bullets — v2.0 (measured numbers only)

House rule: **every number here is produced by a command in this repository and
linked to the artefact that quotes it.** No range estimates, no "improved
performance", no percentages without a denominator. If a number is unmeasured, it
does not appear on a resume — it appears in an interview as "not measured, and
here is what I would do instead".

Use 3–5 of these depending on the role. The mapping table is for you, not for the
resume.

## Bullets

1. Built and shipped a privacy-first mental-wellbeing app (FastAPI, React/TypeScript,
   PostgreSQL) with a **deterministic crisis-detection gate that runs before any
   language model**, reaching **recall 1.000 (95 % CI 1.000–1.000) at precision
   0.543** on a 116-case synthetic evaluation split, and kept a **rules-only
   recall floor of 0.333** as a separate CI gate so model gains cannot hide rule
   rot.

2. Designed an **ML platform** (DVC-versioned splits, config-validated training,
   MLflow tracking, release gates) whose gates fail the build on **fairness gaps
   above dimension-specific ceilings (largest measured gap 0.026 against a 0.10
   ceiling across 11 identity dimensions)**, model-size/quantisation limits, and
   non-reproducible results; **553 tests** run offline in under two minutes.

3. Trained and evaluated a multilingual emotion model and, when the **neural
   sequence model lost to a seven-day moving average** (MAE 0.561 vs 0.554 on
   held-out synthetic users), **shipped the baseline and documented the negative
   result** rather than the more impressive model.

4. Shipped **“Private Mode”: the product's safety engine compiled to JavaScript
   and running in the browser with no network**, verified by a **500/500 phrase
   parity gate** against the Python engine and a Playwright run with the network
   blocked that recorded **0 requests** during a conversation.

5. Improved retrieval with hybrid BM25 + dense fusion and a cross-encoder, taking
   **MRR from 0.639 to 0.658 and Recall@1 from 0.292 to 0.354** on a frozen
   52-query split for **+5.5 ms p95**, and **declined to ship** a higher-MRR
   variant (0.707) that could not abstain.

6. Kept a mobile PWA inside hard budgets: **133.1 KB gzip initial JavaScript of a
   135 KB allowance**, **744.5 KB precache of 800 KB**, every heavy feature
   (WebXR, in-browser models, charts) lazy-loaded as its own route chunk, with a
   script that walks the real import graph and fails CI on regressions.

7. Built a **40/40-passing agentic evaluator** for a tool-using care navigator,
   including prompt-injection, confirmation-gating and out-of-scope scenarios,
   and gated effecting tools behind **uuid4 confirmation tokens with a 15-minute
   TTL**.

8. Wrote the safety and privacy architecture: **AES-GCM envelope encryption** for
   message text, journal and mood notes, a **consented, expiring clinician-share
   link whose decryption key never reaches the server**, uniform 404s to prevent
   link enumeration, and a **21-risk register** with owners and gates.

9. Implemented **12 runtime kill switches** (flip without a restart, audited,
   with a tested safe fallback per flag) and **privacy-only drift monitoring**
   (length histograms, language mix, embedding centroid, PSI/KS alerts) that
   stores **no text and no per-user rows**.

10. Verified the release the way a sceptic would: **5,524 backend tests and 2,917
    frontend tests passing, coverage 94.28 % statements / 85.50 % branches**,
    live-pipeline **lint/type checks clean** (ruff, mypy over 418 files, ESLint,
    `tsc`), and a **13-step end-to-end journey** (onboarding → crisis → mood →
    outlook → exercise → journal → agent → clinician share → export → delete)
    driven against a real database.

## Mapping — where each number comes from

| Bullet | Number | Artefact | Command |
| --- | --- | --- | --- |
| 1 | recall 1.000 / precision 0.543 / rules 0.333 | `evals/reports/2026-10-20/safety.md` | `make eval` |
| 2 | 11 dimensions, gaps 0.008–0.026, ceilings 0.08–0.15; 553 tests | `ml/reports/fairness.json`, `ml/configs/release_gates.yaml` | `python ml/scripts/check_fairness_gates.py`; `make ml-test` |
| 3 | MAE 0.561 vs 0.554 (run `37eebe38…`) | `ml/reports/day36-mood-forecasting.md` | `make ml-mood-forecast` |
| 4 | 500/500 parity, 0 network requests | `docs/verification/day41-browser-private-ai.md` | `make parity`; `npx playwright test e2e/private-mode.spec.ts` |
| 5 | MRR 0.639→0.658, R@1 0.292→0.354, p95 +5.5 ms, 0.707 not shipped | `ml/reports/day35-retrieval.md` | `make ml-retrieval-eval` |
| 6 | 133.1 KB of 135 KB, 744.5 KB of 800 KB | `docs/performance.md` | `npm run build && npm run budget` |
| 7 | 40/40 scenarios | `evals/reports/2026-10-20/agent_eval.json` | `make eval-agent` |
| 8 | 21 risks | `docs/ai-risk-register.md` | — (document) |
| 9 | 12 flags, PSI/KS, no text stored | `docs/verification/day48-verification.md` | `pytest tests/unit/test_kill_switches.py tests/unit/test_drift` |
| 10 | 5,524 / 2,917 tests, 94.28 % / 85.50 %, 13 steps | `docs/verification/day49-verification.md` | `make test`; `python3 scripts/verify_day49_journey.py` |

## Phrases to avoid (they were in an earlier draft of this file, and they were not true)

- "improved safety by 40 %" — no such measurement exists; recall was already 1.000.
- "reduced hallucinations" — there is no hallucination metric in this repository.
- "production scale" / "serving N users" — the deployment has not served real traffic.
- "clinically validated" — it has not been reviewed by a clinician, and saying so
  would be the single most damaging mistake in an interview.
