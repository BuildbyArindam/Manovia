<!-- manovia-eval-summary -->
### Manovia Evaluation Harness — `PASS` (2026-10-20)

| Suite | Metric | Score | 95% Bootstrap CI | Gate |
| --- | --- | --- | --- | --- |
| **Safety (Ensemble)** | HIGH+IMMINENT recall (dev, n=116) | **`1.000`** | `[1.000, 1.000]` | `PASS` |
| **Safety (Ensemble)** | HIGH+IMMINENT precision | `0.543` | `[0.441, 0.645]` | info |
| **Safety (Rules only)** | HIGH+IMMINENT recall | **`0.333`** | `[0.204, 0.471]` | `PASS` |
| **Emotion (9-class)** | Macro-F1 (n=162, en/hi/bn) | **`0.804`** | `[0.739, 0.853]` | `PASS` |
| **Response (60 scenarios)** | All-6 deterministic checks pass rate | **`1.000`** | `[1.000, 1.000]` | `PASS` |

- **Regression diff vs `evals/reports/safety_2026-10-13.md`**: `0` recall/escalation regressions detected.
- **Caveat**: Synthetic benchmark metrics verify engineering invariants and catch regressions; they are not clinical efficacy claims (see `docs/evaluation-report.md`).
