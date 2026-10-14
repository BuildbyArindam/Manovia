# Day 34 verification — compression, serving and safe model rollout

Every command below was run in the sandbox on 2026-10-14 (2 CPUs, Python
3.11, onnx 1.23.2, onnxruntime 1.31.0, no GPU). The sandbox student is
the hashed-feature linear student distilled from a TF-IDF stand-in
teacher on the hand-written toy fixtures (train==eval, 80 safety /
84 emotion rows): **all quality numbers are plumbing**, and the reports
say so. Latency and size numbers are real measurements of the code that
would serve the real MiniLM student.

## 1. Comparison table — fp32 vs distilled vs INT8

`python -m manovia_ml.compression.pipeline --config configs/distill_safety_smoke.yaml --out /tmp/day34-safety`
(and the emotion twin). Same table in `ml/reports/day34-*.md`.

Safety (frozen fixture: 80 hand-written rows):

| model | size (MB) | p50 (ms) | p95 (ms) | safety crisis recall | accuracy |
| --- | ---: | ---: | ---: | ---: | ---: |
| teacher (sklearn_tfidf) | 0.0793 | 0.5883 | 0.7288 | 1 | 1 |
| student fp32 (ONNX) | 0.0198 | 0.1103 | 0.1771 | 1 | 1 |
| student INT8 (ONNX, dynamic) | 0.0058 | 0.1126 | 0.1673 | 1 | 1 |

Emotion (84 toy rows, six of nine labels):

| model | size (MB) | p50 (ms) | p95 (ms) | emotion macro-F1 | accuracy |
| --- | ---: | ---: | ---: | ---: | ---: |
| teacher (sklearn_tfidf) | 0.0949 | 0.6846 | 0.9352 | 1 | 1 |
| student fp32 (ONNX) | 0.0237 | 0.1024 | 0.1835 | 1 | 1 |
| student INT8 (ONNX, dynamic) | 0.0068 | 0.1294 | 0.2103 | 1 | 1 |

Release gates on the safety candidate: **PASS** (all 8 checks; crisis
recall drop INT8-vs-fp32 0.0000 against an allowed 0.02).

## 2. Release gates reject a deliberately degraded model

Student weights with the crisis columns reversed, scored on the fixture:
crisis recall **0.0**, accuracy 0.2.

```
FAIL  safety.min_crisis_recall — crisis recall 0.0 vs floor 0.8
FAIL  safety.max_crisis_recall_drop_vs_teacher — drop 1.0000 (allowed 0.0500)
FAIL  safety.max_crisis_recall_drop_int8_vs_fp32 — drop 1.0000 (allowed 0.0200)
FAIL  safety.min_accuracy — accuracy 0.2 vs floor 0.6
GATES: FAIL (candidate rejected)        (exit 1)
```

Promotion without gates also refuses (`GateRejectedError`,
`tests/test_compression_registry.py`).

## 3. Shadow mode — 200 synthetic messages, no user impact

API started with `SAFETY_ML=baseline` (production) +
`SAFETY_SHADOW_ENABLED=true` + INT8 candidate, migrated SQLite:

```
wrote 200 responses to /tmp/day34-responses-shadow-on.json
total comparisons: 200
  agree: 120
  candidate_higher: 20
  candidate_lower: 20
  candidate_crisis_only: 20
  production_crisis_only: 20
  candidate_timeout: 0
  candidate_error: 0
disagreement rate: 0.400
candidate latency ms: p50=8.741 p95=10.424
```

Log lines carry only `category / production_level / candidate_level /
candidate_latency_ms / *_version` — `grep` for message fragments in the
log: **0 hits**. Restarted with shadow off, same 200 messages:
**responses identical: True (n=200)**.

## 4. Corruption — checksum failure + fallback

- `RegistryLoader.load` on byte-corrupted production: `ChecksumMismatchError
  … refusing to load`; `load_with_fallback` serves previous version v1.
- Through the real factory (`SAFETY_ML=onnx`, corrupt registry):
  `classifier version: v1+onnx-unavailable` (Day-9 baseline, enabled);
  restored bytes: `onnx-v1`, predicting.
- Registration itself refuses a corrupt artefact (registry test).

## 5. Rollback — production alias moves back

Real MLflow file store (`scripts/verify_day34.py`):

```
PASS  promoted v1
PASS  promoted v2          (previous_production=v1, archived=[v1])
PASS  rollback restores v1 {"production": "v1", "previous_production": "v2", ...}
PASS  resolve(@production) is v1
```

## 6. Load test — safety endpoint, 50 concurrent

500 requests, concurrency 50, `POST /api/v1/crisis/assess`, rate limits off
(the load-test-only setting), migrated SQLite, 2-CPU sandbox:

| configuration | errors | throughput | p50 | p95 |
| --- | ---: | ---: | ---: | ---: |
| endpoint, baseline, 1 worker | 0 | 92.5 rps | 454.8 ms | 1086.4 ms |
| endpoint, sequential (any config) | 0 | — | ~11 ms | 12.5 ms |
| ONNX runtime in-process, 50 concurrent | 0 | — | 9.45 ms | **16.24 ms** |
| ONNX runtime in-process, sequential | 0 | — | 5.62 ms | 5.73 ms |

Reading: the **Day-34 model path passes the p95 < 80 ms target with
margin** (16.2 ms at 50 concurrent; live shadow candidate p95 10.4 ms).
The end-to-end endpoint number at 50 concurrent in this sandbox does
not: it is queueing on 2 CPUs behind the rules engine, the TF-IDF
baseline and a single SQLite writer — pre-Day-34 stack, unchanged by
this branch (the baseline configuration shows the same inflation).
Production sizing (workers × Postgres) is the owner follow-up in
PROGRESS.md; it is not hidden behind the model-path number.

## PASS/FAIL table

| # | check | result |
| --- | --- | --- |
| 1 | comparison table (fp32/distilled/INT8, size, latency, recall/F1) | PASS (plumbing quality, real size/latency) |
| 2 | gates reject deliberately degraded model (exit 1) | PASS |
| 3 | 200 shadow messages; disagreement by category; no raw text | PASS |
| 3b | user-visible responses identical shadow on/off | PASS |
| 4 | corrupt model → checksum refusal + fallback | PASS |
| 5 | rollback restores previous production alias | PASS |
| 6 | load test 50 concurrent — model path p95 < 80 ms | PASS (16.2 ms) |
| 6b | load test 50 concurrent — end-to-end endpoint p95 < 80 ms in sandbox | FAIL (sandbox capacity; see §6) |
