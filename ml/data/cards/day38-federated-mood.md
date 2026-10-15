# Dataset card — synthetic federated mood pairs (Day 38)

| Field | Value |
| --- | --- |
| Version / status | 0.1, 2026-10-15; frozen smoke config (not a clinical dataset) |
| Catalogue | `ml/data/CATALOGUE.md` §17 |
| Owner / authors | Manovia contributors, authored generator and pairing code |
| Licence / evidence | MIT, repository `LICENSE`, checked 2026-10-15; retain notice for redistribution |

## 1. What it is

Generated numeric next-day mood pairs for comparing centralised, federated and
record-private optimisers. Not a sample of Manovia users or a diagnostic dataset.
No language/text or demographic attributes; language code `zxx`.

## 2. Provenance

Synthetic in-repo Day-36 `state-space-v1`, paired by `federated/data.py` on
2026-10-15. Command: `make ml-federated-sim`. No download or external platform
terms; no personal data, no real person's consent needed for these invented
numbers. No source-user text is read. No dataset bytes are committed or persisted.

## 3. Contents and identity

- 50 synthetic clients × 40 calendar days smoke; missing endpoints skipped.
- Seed 38: 908 nonoverlapping observed pairs, 655 train / 253 held-out.
- SHA256 of ordered float32 train/test tensor bytes:
  `78a729e3f329137481e1c6436260411f33a12c9af70d92db5b8b875a8f9cd5cc`.
- Inputs: current valence, energy (1–5, fixed normalisation `(x-3)/2`), sleep
  flag, exercise flag, sine/cosine weekday. Label: normalised next-day valence.
- Label quality: generator output, no human/clinical annotation or inter-rater
  agreement. Scale 1–5 is a synthetic numeric target, not a diagnosis.
- Full config generates 210 days; size/hash vary with seed and are logged.

## 4. Known biases

Hand-chosen baseline, weekly/trend/context and AR(1) parameters, perfect numeric
labels and restricted feature set. No demographic, language, device, cultural or
clinical representativeness. Per-user logging jitter still produces missing
observations at base logging probability 1. Dropout is synthetic independent
round dropout; no claim about connectivity bias or deliberate adversaries.

## 5. Fitness

| Use | Allowed? | Notes |
| --- | --- | --- |
| Training a shipped model | No | Research boundary, no validated utility |
| Evaluation / benchmark | Toy only | Sanity/contract checks, not real-world evidence |
| Research comparison | Yes | Always disclose synthetic prior and split |
| Committing dataset bytes | No | Only code, config, fingerprint, statistics and cards |

## 6. Handling

Records live transiently in process memory and disappear at process exit; no
cache to delete or external storage credentials. MLflow retains only hashes,
config, dependency/code metadata and aggregate metrics/figures, never examples
or model parameters. Local reports can be deleted normally; ignored `ml/mlruns/`
is local experiment storage, not a deployment registry.

## 7. Splits

One pair uses days 0/1, 2/3, etc. No calendar day enters two examples; missing
endpoints drop the pair. Fixed-seed random within-person holdout (~30%, rounded
down per client) leaves the rest for training. Pair-identity regression checks
train/test disjointness and no reused raw days. The test set is not used to
select the operating point. IID controls shuffle only training rows. Repeats
change the seed deliberately and record new hashes. No prospective time split
or unseen-person evaluation is claimed; neither calibration nor dev split is
needed for this predeclared smoke (n/a).

## 8. Review record and sign-off

Engineering review: generator provenance, licence and numeric split checks via
catalogue and real-library tests on 2026-10-15. No independent clinician, privacy
specialist, native-language or demographic review; no such validation claimed.
Approved for synthetic engineering smoke only by ADR 0038. Not approved for
clinical/product use; fresh review and cards required before any scope change.
