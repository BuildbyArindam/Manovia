# Federated personalisation — Day 38 research design

Status: **offline simulation only; no phone implementation or deployment**.
Decision: [ADR 0038](adr/0038-privacy-preserving-ml.md). Evidence:
[synthetic report](../ml/reports/day38-federated.md),
[model card](../ml/reports/day38-model-card.md), and
[verification](verification/day38-federated.md).

The claim register below is exhaustive for this design: **M** = measured/tested,
**D** = design requirement, not implemented or measured, **L** = explicit limit.
D rows must not be described as product guarantees. The threat model discusses
residual risks, not additional guarantees.

| ID | Claim / requirement | Class | Evidence or release blocker |
| --- | --- | --- | --- |
| M1 | The simulation has 50 clients; non-IID means one synthetic person's disjoint mood pairs per client. The IID control redistributes the same training rows; test rows are unchanged. | M | `federated/data.py`, missingness/IID regression test; report counts, hash and mean-target SD. Random within-person holdout, not unseen-user evaluation. |
| M2 | Serial in-process Flower `FedAvg.aggregate_fit` / `FedProx.aggregate_fit` perform sample-weighted aggregation; the FedProx local objective adds mu/2 times squared distance from round-start parameters. | M | Real-library weighted toy test, one-step central equivalence integration test, proximal-drift test. This is not Flower's distributed/Ray runtime. |
| M3 | Independent round dropout at 0 and 0.3 and IID/non-IID controls produce measured per-round MASE and participant counts. | M | Report and SVG; all-dropped regression carries prior weights. No evidence for adversarial or mid-upload dropout. |
| M4 | Central Opacus DP-SGD clips joint per-pair gradients and uses Poisson minibatches with RDP accounting. The sweep records individual epsilon/delta and attack AUC. | M | Three clipping-bound tests exercise actual Opacus gradients; accountant monotonicity tests; repeated end-to-end run. Budgets protect one pair, not a person. |
| M5 | One tiny loss-score membership attack measures non-private and DP AUC. | M | Report, negative-loss direction/chance regression. This does not establish attack resistance; one seed and weak attack, no inferential significance. |
| D1 | A phone would retain numeric mood history and a small forecast head locally, infer offline, and personalise the head only. Text, crisis detection, LLM and the intervention policy are outside training. | D | No phone trainer, secure storage or inference measurements. Must test latency, memory, accessibility and deletion on real Android/iOS devices. Existing rules-first safety ordering must stay unchanged. |
| D2 | An eligible phone would clip its entire parameter delta to a reviewed L2 bound, then add calibrated Gaussian noise **before upload** with a cryptographic RNG. Uploads would contain only clipped-then-noised updates plus minimum protocol metadata (round/model version, ephemeral key). No raw records, labels, stable user ID, per-client metrics or exact private sample count. | D | Not implemented. Today's FL simulation exchanges **unclipped/unnoised** weights in one trusted process; today's DP experiment is separate and central. Gaussian noise can increase the transmitted norm: only the pre-noise delta is bounded. A new user-level accountant and noise validation are mandatory. Fixed client weighting would replace today's sample weights. |
| D3 | Secure aggregation would hide individual updates from the coordinator and reveal an aggregate only after at least 30 eligible contributions out of 50; incomplete cohorts would abort without revealing a smaller aggregate. | D | Protocol/key exchange, malicious-server consistency checks, collusion threshold, key erasure and dropout recovery are unimplemented. Obtain an audited Flower SecAgg+ integration and independently review it. Secure aggregation alone is not DP and does not prevent poisoning. |
| D4 | Participation would be explicit, separate, revocable opt-in, off by default, with plain-language scope and a privacy-budget cap. Opt-out would stop queued work/uploads and erase local personalisation and ephemeral keys. | D | No UI/state machine or deletion tests yet. Previously contributed aggregate information cannot be recalled; deletion is not machine unlearning. Repeated participation and re-consent must share one budget ledger. |
| D5 | Training would run only while charging, battery >=80%, unmetered Wi-Fi, thermal state normal, and foreground inactivity; cap a job at 2 minutes and 10 MB, once per day, cancel on any condition change. Never use background training to delay a crisis card. | D | Proposed conservative policy values, not measured battery/network guarantees. OS background scheduling, resumability and thermal tests required. No telemetry content or persistent identity to enforce caps. |
| D6 | Versioned signed model/schema allow-lists, bounded local work, cohort admission controls, aggregate anomaly checks and rollback would mitigate poisoning; traffic would use authenticated encrypted transport and anti-replay round IDs. | D | Not implemented or penetration-tested. Secure aggregation limits per-client inspection; no claimed robust aggregator, Byzantine tolerance, Sybil prevention or attestation. Review the tension between anonymous admission and abuse controls. |
| L1 | No claim of clinical utility, real-data accuracy, fairness, deployment readiness, measured battery saving, transport confidentiality, secure aggregation, or user-level/local/distributed DP is made. | L | No supporting measurements exist for these claims. All records are MIT-authored synthetic numeric data (catalogue §17); no real user data or model weights are committed. |
| L2 | Synthetic record-level epsilon is not a production privacy budget. | L | Fixed public normalisation and nonoverlapping pairs avoid overlapping-record accounting, but many pairs belong to each person. Production would need adjacency review, composition over rounds/models/selection/metrics, cryptographic randomness, opt-out semantics and independent security review. |
| L3 | AUC near 0.5 is neither proof of DP nor proof that inversion, linkage, attribute inference or stronger attacks fail. | L | Only a continuous negative squared-loss score is measured. White-box attacks, shadow models, inversion and poisoning success rates were not measured. |

## Release gates (unmet)

D1–D6 require implementation and direct measurement, independent privacy/security
review, plus a lawful, consented data programme and clinical/safety review before
any experiment could reach users. The simulator accepts only generated data;
there is deliberately no production-data ingestion switch. Full synthetic
training on Colab/Kaggle is the next research step, not a release gate substitute.
