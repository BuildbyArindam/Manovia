# ADR 0038 — Privacy-preserving ML research boundary

Date: 2026-10-15. Status: accepted for offline research, **not deployment**.

## Context

Day 38 needs inspectable Flower FedAvg/FedProx, Opacus privacy accounting, and a
membership attack without collecting wellbeing records or training large models
in the CPU sandbox. Day 36 already supplies a reproducible synthetic generator.

## Decision

- Reuse that generator, with a small 6→8→1 tanh next-day mood regression head.
  Nonoverlapping day pairs prevent a single raw observation appearing in two
  record-level DP training examples. Fixed public scaling has no fitted stats.
- One person's pairs per client; 50 clients. Compare to a same-row IID shuffle,
  independent round dropout, and a full-cohort centralised baseline. Flower
  strategy aggregation runs synchronously in-process, no Ray/RPC claims.
- Keep central Opacus DP-SGD separate from FL. RDP accounting uses actual
  Poisson-loader steps. No claim that FedAvg/FedProx are private; no claim of
  user-level DP. Seeded RNG is research-only. No weights/data persisted.
- Predeclare sigma=3.2, C=1 as a conservative research point before reading the
  holdout (highest sweep noise, allows more gradient signal than C=0.5).
  Do not tune against test metrics or change the attack to get a desired AUC.
- Log config, data fingerprint, code state, dependencies and aggregate results
  to MLflow; commit report, SVG, cards and instructions only. Real training is
  deferred to the multi-seed Colab/Kaggle script.
- Keep backend and ML virtual environments separate. Flower 1.40.0 requires
  uvicorn <0.50, incompatible with the backend's 0.54 pin. Do not downgrade the
  backend or bypass dependency checking. Torch 2.9.1 matches the training extra.

## Consequences

The smoke verifies plumbing and finite-budget calculations, not utility or an
empirical privacy defence. DP and central/FL have different optimizer budgets;
MASE comparisons are descriptive. There is no local/distributed DP, secure
aggregation, adversarial robustness or product integration. Every proposed phone
control is labelled unmeasured in the exhaustive design claim register.

See `docs/federated-design.md`, catalogue §17 and `ml/reports/day38-model-card.md`.

## Verification follow-up

Final review found sweep arms initially reset the same noise seed. They now use
setting-specific derived streams, pinned by a regression test; a production
composition claim additionally assumes fresh independent secret randomness, not
public seeded research. The operating point remains predeclared, not test-picked.
The full backend command exposed the existing Day-37 safety coverage deficit;
focused tests cover corrupt metadata, optional local-loader boundaries and
trained/ONNX selector degradation. No backend production code or coverage floor
was changed. The ML CI job now installs the federated extra and runs the smoke
so the real Flower/Opacus tests cannot silently skip there.
