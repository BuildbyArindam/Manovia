# Model card — Day 38 synthetic privacy experiments

| Field | Value |
| --- | --- |
| Card version / status | 0.1, 2026-10-15; experimental, do not deploy |
| Run id | `a45a78eaa2d64ac5b90298a8a7fc4da9` (local MLflow) |
| Config | `ml/configs/federated_smoke.yaml` |
| Config SHA256 | `8f2a5f360b2022afcdff474bebecf2c9c27f34c39699e57f86683e795fd76998` |
| Code | `1e7e9b7026ee737bf3234702998812206f4fe729`, dirty **false**, branch `arena/e17d4e5d-manovia` |
| Seed / hardware / duration | 38, CPU only, one torch/BLAS thread; MLflow span 5.077 seconds (not device performance) |
| Owner | Manovia contributors; ADR 0038 |
| Runtime | Python 3.11.2; torch 2.9.1, Flower 1.40.0, Opacus 1.5.4, NumPy 2.4.6, sklearn 1.9.1, MLflow 3.17.1 |

## 1. What it is

A fixed 6→8→1 tanh next-day numeric mood regression head, trained centrally,
with serial in-process Flower FedAvg/FedProx, or separately with central Opacus
DP-SGD. Weights are transient; no model artifact is saved or deployable. Flower
strategies own weighted aggregation; local proximal training is repository code.

## 2. Intended use

Offline engineering/experiment comparison only, consumed by researchers. Not
therapy, diagnosis, validated mood prediction, an emotion/crisis classifier or
a recommendation policy. Failure produces a bad synthetic metric, not a message
to a user. No API, phone, crisis-gate or intervention-policy integration exists.

## 3. Data

Catalogue §17; [dataset card](../data/cards/day38-federated-mood.md). MIT-authored
Day-36 numeric state-space generator, 50 synthetic people, 40 days, no real text
or identifiers. 655 training / 253 held-out observed nonoverlapping pairs; random
within-person split (no unseen-person/prospective claim). Data SHA256:
`78a729e3f329137481e1c6436260411f33a12c9af70d92db5b8b875a8f9cd5cc`.
Missing endpoints are discarded, no label imputation. Fixed public scaling.
No validation split is used: all hyperparameters and the operating point are
predeclared. DP adjacency is add/remove one pair, not a person's full history.

## 4. Evaluation

Full 15-arm table, convergence and participating-client counts:
[day38-federated.md](day38-federated.md) and [figure](day38-federated.svg).

| Arm | held-out MASE (n=253) | loss-score membership AUC (253+253) | epsilon / delta |
| --- | ---: | ---: | --- |
| Persistence (current-day mood) | 1.1150 | n/a (not trained) | n/a |
| Centralised (8 full-cohort updates) | 1.3372 | 0.5518 | not DP |
| FedAvg non-IID, no dropout | 1.3381 | 0.5521 | not DP |
| FedProx non-IID, no dropout | 1.3384 | 0.5520 | not DP |
| Predeclared DP sigma=3.2 C=1 (44 Poisson updates) | 1.2754 | 0.5459 | 0.8417 / 1e-5 |

The head underfits this smoke and does **not** beat persistence. DP uses more
optimizer steps than central/FL; its lower MASE does not establish a DP utility
advantage. The observed AUC movement is only 0.0059, not a statistically supported
empirical defence. Other sweep AUCs are reported without favourable-seed/model
selection. No classification confusion matrix or categorical accuracy applies.
Training losses/per-example scores are not persisted; the aggregate report is
the evaluation artifact, with no private-data release claim.

Reproduce: `make ml-federated-sim`, or the script and five-seed full config in
`ml/README.md`. The integration test reproduces all rows exactly at a fixed seed;
tolerance is 1e-6 for the one-step central/FedAvg equivalence. The larger synthetic
training experiment has **not** been run locally.

## 5. Limits, privacy and biases

- One hand-authored generator and one smoke seed: no uncertainty, external,
  clinical, demographic or language generalisation claim. Full multi-seed and
  stronger predeclared attack evaluation are outstanding.
- Central RDP epsilon/delta is the accountant's budget for the corresponding
  randomised mechanism. Public seeded noncryptographic research noise is **not
  a production privacy guarantee**. Setting-specific derived streams prevent
  tape reuse across sweep arms; production needs independent secret randomness.
- Each row is a separate hypothetical model release, not one shared epsilon.
  Basic composition (assuming fresh independent secret noise) over six releases
  is epsilon ≤22.3396, delta ≤6e-5, before any private metric/selection release.
- No local, user-level or federated DP, secure aggregation, inversion or poisoning
  defence is implemented. An individual contributes multiple correlated pairs.
- AUC near chance for one weak attack is not a security proof. Stronger attacks
  and confidence intervals are outstanding. Independent dropout is not an
  adversarial robustness test. FedProx does not defend against backdoors.

## 6. Languages and fairness

No text training or language evaluation. English, Hindi, hi-Latn and Bengali are
**not supported/evaluated** by this head. No fairness or clinical review occurred.
No demographic groups exist in this experiment, so group metrics are n/a.

## 7. Safety relationship

No product imports this module: it cannot lower a safety level, see a real user
identifier or run on HIGH/IMMINENT traffic. There is no output guard because
there is no user-facing output. The existing rules-first safety flow is unchanged.
No clinician sign-off. The phone requirements are explicitly unmeasured in
`docs/federated-design.md`.

## 8. Decision

**Do not ship.** ADR 0038 accepts synthetic offline research only, on 2026-10-15.
Revisit only after multi-seed reproducibility, validated consented data, a
user-level/secure-aggregation protocol, privacy/security audit, phone measurements
and clinical/safety review. Existing wellbeing product routes must remain separate.
