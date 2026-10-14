# Compression report — `<model name / version>`

> Template (Day 34). Filled by `manovia_ml.compression.report` from measured
> JSON; a hand-typed number in this table is a review failure. The smoke
> report must carry its stand-in-teacher label in the title notes.

| model | size (MB) | p50 (ms) | p95 (ms) | safety crisis recall | emotion macro-F1 | accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| teacher (`<kind>`) | | | | | | |
| student fp32 (ONNX) | | | | | | |
| student INT8 (ONNX, dynamic) | | | | | | |

Release gates (`ml/configs/release_gates.yaml`): **PASS / FAIL**

- [ ] `safety.min_crisis_recall` —
- [ ] `safety.max_crisis_recall_drop_int8_vs_fp32` —
- [ ] `serving.max_p95_latency_ms` —
- [ ] `size.*` —

## Notes

- Teacher kind / artefact run id: `<...>`
- Frozen eval set + sha256 + rows: `<...>`
- What is plumbing vs real in this report: `<...>`
