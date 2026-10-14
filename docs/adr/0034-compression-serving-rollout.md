# ADR 0034: Compression, serving and safe model rollout — distillation, ONNX INT8, registry aliases, shadow mode

- Status: accepted (Day 34)
- Date: 2026-10-14
- Deciders: Manovia maintainers
- Supersedes: none. Extends [0031 ML platform](0031-ml-platform.md), [0032 emotion LoRA](0032-emotion-lora-finetune.md) and [0033 safety trained classifier](0033-safety-trained-classifier.md) with the path that makes a trained model small, servable and safely replaceable.
- Related: [0008 crisis rules engine](0008-crisis-detection-rules-engine.md), [0009 safety ML ensemble](0009-safety-ml-ensemble.md), [0028 model card gate](0028-model-card-and-clinical-review-gate.md), [`ml/configs/release_gates.yaml`](../../ml/configs/release_gates.yaml), [`docs/runbook.md`](../runbook.md) (model rollback)

## Context

Days 32–33 produce large teachers: a LoRA fine-tune of `xlm-roberta-base`
(~278M parameters, ~1.1 GB fp32) for emotion and for safety. Serving that
on a small CPU container per message is not credible, and replacing any
production model without a gate, a checksum and a rollback is not safe.
Day 34 is the compression + serving + rollout layer. Constraints are the
standing ones: no GPU or hub access in the build sandbox, no weights or
datasets in Git, config as data, every number from a frozen eval, and the
rules engine stays the gate — a compressed model may only ever *raise* a
level through the existing ensemble (ADR 0009).

## Decision

### 1. Distillation with the temperature loss, two student kinds

Loss `alpha * T^2 * CE(teacher_T, student_T) + (1-alpha) * CE(y, student_1)`,
all parameters in `DistillConfig` (`ml/configs/distill_*.yaml`).
`student.kind: transformer` (multilingual MiniLM-L12-H384 class) is the
real student and runs on Colab/Kaggle from
`ml/notebooks/colab_distill_export.ipynb`. `student.kind: linear_hash`
(hashed n-gram logistic regression trained in numpy with the exact loss)
is the offline smoke student that proves the whole path in the sandbox.
The sandbox teacher is a TF-IDF stand-in because the Day-32/33 weights do
not exist here; every report names the teacher kind so a smoke number
cannot be quoted as a teacher number.

### 2. ONNX + INT8 dynamic quantisation, gated on frozen-eval recall

The student exports to a `Gemm → Softmax` ONNX graph over hashed features
(the featurizer spec travels in the manifest and is mirrored in the
backend, pinned by golden vectors). INT8 is ONNX Runtime
`quantize_dynamic` (QInt8 weights). fp32 and INT8 are scored on the same
frozen eval rows; `ml/configs/release_gates.yaml` bounds the trade —
notably INT8 crisis recall may drop at most **0.02** against fp32, and
absolute crisis recall must stay at or above **0.80**. A candidate that
fails any gate is rejected: the gate check exits non-zero and promotion
refuses without a passing report (`GateRejectedError`). On the sandbox
fixture the INT8 file is ~3.4× smaller than fp32 with identical recall;
that ratio is fixture-specific and the report says so.

### 3. Serving in the backend, behind the existing interface

`backend/app/services/ml_runtime/` loads `@production` from the
materialised registry, verifies the manifest SHA-256 *before* building a
session, warms up on startup, batches concurrent singles dynamically,
runs on an owned thread pool with a per-model timeout, and falls back
**production → previous version → the pre-Day-34 classifier**. `SAFETY_ML=onnx`
selects it through the same factory as `baseline`/`trained`, so the
ensemble, the `max(rules, ml)` invariant and every caller are unchanged.
Target: CPU p95 < 80 ms/message for the safety path (release gate +
load-test evidence in `docs/verification/day34-compression-serving-rollout.md`).

### 4. Registry: MLflow aliases are the stages

MLflow 3.x removed registry stages for aliases, so staging/production/
archived map onto `@staging`/`@production` plus an archived record.
Registration verifies the checksum before recording it as a version tag.
Promotion additionally materialises the version into the serving
registry directory the backend reads (`aliases.json` + `versions/<v>/`),
records the displaced production version as `previous_production`, and
requires passing gates. Rollback is one alias move back — no retraining,
no redeploy of code. The backend loader never imports MLflow.

### 5. Shadow mode changes nothing, by construction

With `SAFETY_SHADOW_ENABLED`, the candidate scores each message after
the production decision is computed; `pipeline.run_ensemble` discards the
shadow result, so the response with shadow on is the response with
shadow off (asserted in tests and in the Day-34 verification: 200
synthetic messages, byte-identical bodies). Only disagreement
*categories* (a closed vocabulary: agree / candidate_higher /
candidate_lower / candidate_crisis_only / production_crisis_only /
candidate_timeout / candidate_error) and latencies are recorded — the
record type has no text field. `backend/scripts/shadow_report.py`
summarises the JSONL log and refuses a line carrying unexpected keys.

## Consequences

- A degraded quantised model now fails mechanically (gate check exit 1,
  promotion refusal) instead of reaching users on an operator's judgement.
- Rollback is a registry alias move; the runbook's model-rollback section
  is the one-command procedure and was exercised in verification.
- The served artefact is checksummed end to end: corruption after
  registration refuses to load and degrades to the previous version or
  the baseline, with the reason in the classifier version string.
- Cost: two featurizer implementations (ML + backend mirror), held
  together by golden vectors; and a materialised registry directory to
  keep in step with MLflow, owned by the promotion code.
- The real MiniLM students do not exist yet: no GPU/hub in the sandbox.
  Their configs, notebook and exact owner instructions are committed;
  every sandbox number is labelled plumbing.

## Verification

See `docs/verification/day34-compression-serving-rollout.md` for the
commands actually run: the fp32/distilled/INT8 comparison table, the
degraded-model gate rejection, 200 shadow messages with identical
responses, the corruption + fallback demo, the rollback alias move, and
the 50-concurrent load test.
