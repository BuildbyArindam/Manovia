# Changelog

All notable changes to Manovia are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Entries before the
first release are grouped by build day (the sequence in
[PROGRESS.md](PROGRESS.md)) and link to the decision record and verification
note for each. Releases are cut by pushing a `v*` tag, which runs
`.github/workflows/release.yml` (migration round-trip on Postgres 16, images
to GHCR, GitHub release).

## [Unreleased]

### Fixed
- The GitHub Pages deploy workflow can start again: `upload-pages-artifact` and `deploy-pages` were pinned to commit SHAs that do not exist, so the job failed while resolving actions (before any build ran). Both are now pinned to the matching v5 releases, and `dist/.nojekyll` is no longer dropped from the artifact.
- The Pages deploy now skips with an actionable warning (instead of a red X nobody can act on) when the repository has not been switched to *Settings → Pages → Source: GitHub Actions*.
- The app works when it is served from a subpath, as a GitHub Pages project site is (`/Manovia/`): the router basename, the static web-manifest URLs and the service-worker app-shell lookup all derive from Vite's base instead of assuming the origin root.
- Refresh-token rotation is atomic: two simultaneous refreshes of one token can no longer both succeed (the loser is treated as reuse and the family is revoked).
- A double-submitted questionnaire (same answers within 10 seconds) is stored once; the item-9 support options are still returned.
- Frontend `ResponseOption.detail` is optional, matching the API.

### Added
- Safety mutation testing runner and report ([docs/mutation-report.md](docs/mutation-report.md)); 63 new boundary tests for the ensemble, escalation, session accumulator and long-message reading.
- OpenAPI contract check for the frontend client (`npm run api:check`, run in CI).
- Locust load test (`backend/loadtest/`) and a repeated-run flaky-test script.
- [ADR 0049](docs/adr/0049-test-hardening-and-load-testing.md).

## [2.0.0] — 2026-10-20

The v2.0 release: the deep-learning and advanced-features track (Days 31–49), a
technical report, an ML platform with release gates, and a release label that
keeps telling the truth — **portfolio demo, not a clinical service**.

Highlights, with the artefact that backs each one:

- **ML platform** (`ml/`, [ADR 0031](docs/adr/0031-ml-platform.md)): licensed
  dataset catalogue (17 datasets, 10 restricted), DVC-versioned splits with
  byte-stable manifests, config-as-data training (`pydantic`, unknown keys are
  errors), MLflow tracking (commit, data hashes, config, seed, versions), release
  gates (reproducibility, size/INT8, fairness, DP budget, licences) and committed
  model/dataset cards. `make ml-test` → 553 passed (497 without the training/ONNX/federated extras).
- **Safety ML that never takes the wheel** ([ADR 0009](docs/adr/0009-safety-ml-ensemble.md)):
  a calibrated TF-IDF backstop that may raise caution and never lower it; rules
  still decide and still write the crisis card. Distilled student + ONNX serving
  path behind `SAFETY_ML` ([ADR 0033](docs/adr/0033-safety-trained-classifier.md),
  [ADR 0034](docs/adr/0034-compression-serving-rollout.md)).
- **Retrieval** ([ADR 0035](docs/adr/0035-fine-tuned-retrieval.md)): hybrid BM25 +
  dense + cross-encoder with a calibrated "no good answer" floor; MRR 0.658,
  Recall@1 0.354 on the frozen 52-query split for +5.5 ms p95.
- **Research that reports its own negative result**: mood forecasting
  ([ADR 0036](docs/adr/0036-mood-forecasting.md)) where the best neural model lost
  to a seven-day moving average (MAE 0.561 vs 0.554) and was not shipped; a
  contextual-bandit policy ([ADR 0037](docs/adr/0037-personalised-intervention-policy.md))
  with a hard 20 % exploration cap, not shipped; federated learning with
  differential privacy ([ADR 0038](docs/adr/0038-privacy-preserving-ml.md)),
  ε 0.8417 at δ 1e-5 in simulation, not shipped.
- **Private Mode** ([ADR 0040](docs/adr/0040-in-browser-private-ai.md)): the same
  safety engine compiled to JavaScript, 500/500 phrase parity with Python, a lazy
  30.9 KB chunk, AES-GCM ciphertext in IndexedDB, and **no network** for the
  conversation. Ships without a pinned model, so replies are templates and the
  badge says so.
- **New tools, each with a safety story**: real-time voice with barge-in
  ([ADR 0039](docs/adr/0039-realtime-voice-companion.md); p95 first-audio 98.6 ms
  on fake stages), HRV-paced breathing ([ADR 0041](docs/adr/0041-biofeedback-hrv.md)),
  a local personal-patterns graph ([ADR 0042](docs/adr/0042-local-personal-patterns.md)),
  an agentic care navigator with read-only tools and confirmation gating
  ([ADR 0043](docs/adr/0043-agentic-care-navigator.md),
  [ADR 0044](docs/adr/0044-directory-mcp-server.md); 40/40 scenarios), generative
  calm audio ([ADR 0045](docs/adr/0045-personalised-meditations.md)), a WebXR calm
  space with a 2D fallback and a sensory profile
  ([ADR 0046](docs/adr/0046-xr-calm-space-and-sensory-profile.md)), and a
  clinician-share bridge ([ADR 0047](docs/adr/0047-clinician-share-bridge.md))
  whose key the server never holds.
- **Responsible AI** (Day 48): 21-risk register with gates, privacy-only drift
  monitoring, **12 kill switches** (flip without restart, audited), a user-facing
  `/transparency` page (14 components × 5 headings, en/hi/bn), fairness gates over
  eleven identity dimensions, and a red-team pass with regression tests.
- **Performance** (Day 49): every heavy module lazy (WebXR 5.8 KB route increment,
  WebLLM/transformers in a 30.9 KB chunk that is not precached, charts 107.8 KB on
  `/insights` only); initial JS **133.1 KB gzip** of a 135 KB budget; precache
  744.5 KB of 800 KB. Budgets and what is *not* measured:
  [docs/performance.md](docs/performance.md).
- **Release engineering**: a one-page architecture poster, a paper-style
  [technical report](docs/technical-report.md), an API-level journey verifier
  (`scripts/verify_day49_journey.py`, 13/13), and a fresh-clone verification note
  (`docs/verification/day49-verification.md`).

### Fixed in 2.0.0 — found by the release verification

- **Fairness report was not reproducible.** Stand-in predictors seeded from
  Python's salted `hash()`, so `ml/reports/fairness.json` changed on every run.
  Now SHA-256-seeded, with a cross-process byte-stability test.
- **A fairness ceiling that never applied.** `max_gap_code_mixing` could not match
  the `code-mixing` category, so that dimension ran against the global 0.15
  default instead of 0.12. Category names are normalised and a test asserts every
  template category has a configured ceiling.
- **`dvc.lock` moved every day.** Safety manifests carried a wall-clock date and
  absolute seed paths. Both removed; manifests have a byte-stability test.
- **Two splitter tests read a gitignored DVC output**, so `make ml-test` failed in
  a fresh clone. They now read the committed fixture (a stronger check).
- **A baseline-availability test branched on the wrong condition** (backend
  importability instead of the imports the code branches on) and failed on a
  machine with `ml` installed and no `nlp` extra. It now stubs the imports, and
  `checkpoint_is_cached` names the checkpoint and the cache in every verdict.
- **`manovia_ml` shipped no PEP 561 marker**, so `make lint` in `backend/` failed
  wherever `ml` was installed. Added `py.typed` + package data.
- **Prometheus route labels collapsed to `:unmatched` on Starlette 1.x.** Starlette
  1.0 stopped writing the matched route to `scope["route"]`, so on a fresh install
  (fastapi 0.143 accepts any starlette ≥ 0.46) every request lost its template.
  Labels now resolve from the app's own route table, with a regression test.
- **An exact-float assertion in the emotion pipeline test** (nine 6-decimal scores
  summing to 1.0 ± 1e-6) failed on some model initialisations; the tolerance now
  matches the rounding.
- **`make ml/lint` failed once the extras were installed**: newer
  sentence-transformers stubs return `int | None` for the embedding dimension.
  An encoder with no width now raises the error the caller already handles.

### Known issues at 2.0.0

1. **Not clinically reviewed**, and no native-speaker sign-off on Hindi/Bengali.
2. **Recall-first costs precision**: 31/51 benign dev cases escalate; in the live
   journey *"I had a long day and I am tired."* produced the crisis card.
3. **No pinned on-device model** (`npm run models:pin` has never run), so Private
   Mode answers with templates.
4. **No Lighthouse / Core Web Vitals / real-device numbers** for this release —
   the build sandbox cannot install a browser.
5. **Four helplines still `needs_verification`**; all entries last verified
   2026-10-09.
6. **Some days are not recorded in this file** (28–34, 42–46, 48–49): their detail
   is in [PROGRESS.md](PROGRESS.md) and their ADRs. This file is being brought
   back in line release by release, rather than rewritten after the fact.

Days 35, 36, 41 and 47 keep their detailed entries below.

### Added — Day 47: clinician-share bridge (consented, time-limited, standards-based)

- **"Prepare for my appointment"** (`/shares`): seven items — mood trend summary,
  screening history, selected thought records, exercise helpfulness, a note,
  questions to ask, "identify me" — all off by default, with a live preview that
  renders the same component the recipient sees. Nothing is shared that was not
  ticked, and the five never-included items are printed on the page
  ([ADR 0047](docs/adr/0047-clinician-share-bridge.md)).
- **Consent that records what was shown**: `POST /api/v1/shares/preview` returns
  the consent lines and their `consent_sha256`, and `POST /api/v1/shares` answers
  `409 consent_mismatch` unless that hash comes back with the same selection.
- **Two exports**: a printable A4 PDF whose banner *"Patient-generated, not a
  diagnosis"* is boxed on page 1 and repeated in every page footer, and an **HL7
  FHIR R4 document Bundle** (Composition first, Provenance last, 16 entries for a
  full selection) in which the **Patient** is the `author` and the Manovia
  **Device** the `assembler`.
- **Verified terminology only**: PHQ-9 total `44261-6`
  (<https://loinc.org/44261-6>) and GAD-7 total `70274-6`
  (<https://loinc.org/70274-6>), read off loinc.org on 2026-10-19 and pinned with
  their source URLs in `backend/app/content/sharing/loinc.json`; every
  Manovia-specific concept is coded in `https://manovia.app/fhir/CodeSystem/…`.
- **Secure share links**: 7-day default TTL (1–30), a 5-view budget (max 25), a
  passcode (PBKDF2-HMAC-SHA256, 210 000 iterations) with lockout after 5 wrong
  attempts, 10 requests/minute on the public routes, revoke one or **revoke
  everything**, and an owner-visible audit line — *"Opened 2 times of 5 allowed."*
- **Encryption whose key the server never holds**: AES-256-GCM per document with
  AAD bound to the token and the document name, under a key derived by
  HKDF-SHA256 from a secret delivered in the URL fragment (`/s/<token>#k=…`), the
  passcode and the token. `clinician_shares` has 23 columns and no key column, and
  only the token's SHA-256 and first 8 characters are stored. The WebCrypto
  implementation is proved equal to the Python one against a committed vector of
  real backend ciphertext.
- **A read-only viewer at `/s/:token`** that needs no account, renders no app
  chrome, and decrypts in the browser — matched before the onboarding gate so a
  professional is never asked to sign up.
- **Uniform refusals**: unknown, expired, revoked and view-exhausted tokens all
  answer the same `404 share_unavailable`, because telling them apart would be an
  enumeration oracle.
- **Tokens redacted from logs and Sentry**: share tokens, secrets and passcodes
  never reach an access log, an unhandled-exception log or a Sentry URL.
- **An operator kill switch** (`SHARE_ENABLED=false` unmounts both routers) and
  privacy wiring: account deletion removes shares, audit lines and the preference;
  retention removes expired rows; a data export contains the decisions and the
  audit trail, never a ciphertext, token or passcode hash.
- **FHIR validation, both halves**: a deterministic committed sample bundle
  (`backend/scripts/export_fhir_sample.py`), a dependency-free structural checker
  (`backend/scripts/fhir_offline_check.py`, 28 tests including 23 mutations it
  must catch), `make fhir-validate` / `make fhir-validator`, and a CI
  `fhir-validation` job that runs the **official HL7 validator**
  (`hapifhir/org.hl7.fhir.core` 7.0.2 `validator_cli.jar`, sha256-pinned,
  temurin 17, `-version 4.0.1 -tx n/a`) on the regenerated sample and gates on its
  exit code — **and has run**: CI run `39337225868`, job success,
  `Success: 0 errors, 52 warnings, 6 notes`, plus a second non-blocking
  invocation against the real terminology server at `https://tx.fhir.org`
  (`0 errors, 43 warnings, 2 notes`). No line of either output mentions LOINC.
- **Twelve new routes** (86 API operations in `docs/api.md`, from 74), twelve
  `SHARE_*` settings validated at startup, migration `0012_clinician_shares`, and
  137 `sharing.*` strings in English, Hindi and Bengali.
- **Tests**: backend 5 113 → **5 458 passed, 4 skipped**; frontend 2 858 →
  **2 917 passed, 3 skipped**; 226 of the backend tests are share tests
  (14 integration, 18 security, 194 unit and artifact) and 57 of the frontend ones
  are share tests — the remaining +2 frontend tests are the biofeedback
  regression below. `ruff check`, `ruff format --check` and strict `mypy` are
  clean, as are `npm run lint`, `npm run typecheck` and `npm run build`.
- **Docs**: [docs/clinical-sharing.md](docs/clinical-sharing.md) (the data flow,
  the limits, and the three sentences about what this is *not* — not an EHR
  integration, not for emergencies, not a diagnosis) and
  [docs/verification/day47-clinician-share-bridge.md](docs/verification/day47-clinician-share-bridge.md).

### Fixed — Day 47

- **`BiofeedbackView` no longer updates state after unmount** (Day-42 code, found
  by Day-47's CI runs). Every render opened IndexedDB from an effect; when a test
  file finished, vitest tore jsdom down while that read was in flight, it
  rejected, and the `catch` called `setTrend([])` with no `window` left — which
  React's update path reads. CI reported
  `Unhandled error :: ReferenceError: window is not defined` and failed the
  frontend job on a commit that had changed nothing but `ci.yml`. A `mountedRef`
  the effect keeps current (StrictMode-safe) now guards both `setTrend` calls, and
  `BiofeedbackView.unmount.test.tsx` reproduces the teardown deterministically —
  verified to exit 1 with that ReferenceError when the guard is removed and 0 with
  it in place. No test was weakened, deleted or skipped.
- **`scripts/export_fhir_sample.py` printed a character count as a byte count.**
  The sample's 10 em dashes are 3 bytes each in UTF-8, so the reported size was 20
  bytes short of `wc -c`. It now reports the encoded size.

### Known limitation — Day 47

- The official HL7 validator could not execute in the development sandbox (no
  JVM, no release download), so it ran in CI instead and reported **0 errors**
  twice over. It is not silent, though: 52 warnings remain, and each is a
  deliberate choice rather than an oversight — 14 resources carry no narrative,
  19 codings use Manovia's own CodeSystem namespaces which are not published
  anywhere a validator can fetch, 6 Observations have no `performer`, and the two
  assessment bands are not in HL7's extensible interpretation value set. They are
  itemised with their reasons in §8 of the
  [verification note](docs/verification/day47-clinician-share-bridge.md), and
  cutting them is Day-48 step 3. Everything else above was verified by a real
  local run.
- This is **encryption at rest with an ephemeral, fragment-delivered key**, not
  end-to-end encryption: the server assembles the plaintext from rows it can
  already decrypt before re-encrypting it. Browser-side assembly is in
  [PROGRESS.md](PROGRESS.md)'s parking lot.
- The PDF cannot render Devanagari or Bengali (reportlab's built-in Helvetica,
  no TrueType font in the repository): those glyphs become `?` with a note printed
  on the page. The HTML viewer and the FHIR bundle are unaffected.

### Added — Day 41: in-browser private AI ("Private Mode")

- A **Private Mode** switch on the chat page. When it is on, no message text goes
  to the server or to any model API: the safety rules, crisis support, output
  guard and reply are all computed in the browser
  ([ADR 0040](docs/adr/0040-in-browser-private-ai.md)).
- A **compiled safety bundle** (191.5 KB, sha256 `9fa233a8ea9ff143`) built at
  build time from `backend/app/content/**` — 215 rules, 66 output rules, 34
  helplines, 3 locales and the 3 system prompts — fetched from this origin with
  credentials omitted and gated for drift on both the Python and TypeScript side.
- A **TypeScript rules engine at parity with Python**: 500 corpus phrases yield
  identical risk levels, categories, ordered rationale codes and context fields
  (500/500, ≈0.9 ms per message), with CPython's case folding, word boundaries
  and Unicode classes reproduced rather than approximated. `make parity` runs the
  six exporter gates, the bundle suite and the frontend private tests.
- **Capability detection** (WebGPU, WASM SIMD/threads, cores, memory, storage,
  secure context) choosing one of three tiers and explaining itself in one
  sentence; at `rules-only` the app still answers offline and offers standard
  mode instead of pretending.
- **Private Mode is loaded on demand.** The chat page keeps a one-line entry point
  and pulls the on-device stack as a chunk of its own the first time the panel is
  opened, so `/chat` stays inside its 25 KB gzip budget (12.7 KB) and the precache
  stays inside 800 KB (791.5 KB): nobody downloads the rules engine, the download
  manager or the crypto unless they ask for Private Mode. The service worker keeps
  the two compiled artifacts after that — network first, because a safety-rule
  change must reach the device — and never model bytes.
- A **consented model download manager**: declared size and an honest note before
  consent, resumable HTTP Range downloads, sha256 verification that fails closed,
  bytes in IndexedDB, pause/resume/cancel, per-model delete and delete-all behind
  a confirmation. The committed manifest is deliberately unpinned, so nothing can
  be downloaded until `npm run models:pin` runs somewhere with egress.
- **Encrypted local transcripts**: PBKDF2-HMAC-SHA256 (210,000 iterations) from a
  passphrase that is never stored, non-extractable AES-GCM-256 keys, per-field
  AAD, message records cleared to routing data plus ciphertext, and no recovery
  path — stated before the key is created.
- A **per-reply honesty badge**: which runtime wrote the answer, whether it was
  stored, and, for crisis and guard-refusal replies, that no model wrote them.
  Voice is disabled in Private Mode and says why.
- **47 new strings under `private.*`** in English, Hindi and Bengali, in the
  lockstep the locale parity test enforces.
- An **end-to-end network proof** (`e2e/private-mode.spec.ts`): with offline
  emulation and a catch-all aborting route, an ordinary message and a HIGH-risk
  crisis message are both answered, the CrisisCard shows real helplines, zero
  requests are attempted, and a raw IndexedDB dump contains ciphertext only.

### Added — Day 36: mood forecasting and opt-in Outlook

- A deterministic **synthetic mood-forecast research pipeline** with explicit user/time
  splits, four baselines, optional GRU/LSTM/Transformer quantile models, held-out
  split-conformal interval expansion, feature ablations, guarded few-shot adaptation,
  MLflow logging and a generated report. PyTorch was unavailable in this sandbox, so
  the measured report is baseline-only; no neural result or improvement is claimed.
- A **default-off, explicit-opt-in Mood Outlook** on Insights, backed by numeric-only
  personal history and gentle English/Hindi/Bengali copy. It withholds estimates without
  14 distinct check-in days in 28 local days or when the latest check-in is over seven
  days old. Its empirical range is not represented as a calibrated confidence interval
  or probability; the endpoint never reads mood notes or journal text.
- A semantic table for the estimate and variation range, accessible without a chart or
  colour cue; an opt-in Settings switch and persisted preference; generated API docs.
- Privacy export v2 adds `user-settings.json`, including the Mood Outlook preference.
- [ADR 0036](docs/adr/0036-mood-forecasting.md),
  [docs/verification/day36-mood-forecasting.md](docs/verification/day36-mood-forecasting.md),
  and `ml/reports/day36-mood-forecasting.md`.

### Added — Day 35: fine-tuned retrieval (embeddings, hybrid search, reranking)

- A **retrieval eval set**: 154 hand-written queries labelled with the document ids
  that answer them, plus 59 no-answer rows (42 out of scope, 17 the corpus lacks),
  in en / hi / hi-Latn / bn / bn-Latn across direct, keyword, vague, scenario,
  hinglish and romanised styles. `ml/retrieval/augment.py` expands it to 254 rows
  with template paraphrases, each flagged `synthetic` and screened for label drift.
  Committed: the hand-written ground truth, its dataset card and catalogue entry 16.
- **Four frozen splits** cut by hashed bucket — train 97, dev 85, calibration 20,
  test 52 (hand-written rows only) — each with a sha256 fingerprint recorded in the
  split manifest *and* in the calibration file a deployment serves.
- **Hybrid retrieval in the backend**: in-house Okapi BM25 (`rag/bm25.py`),
  reciprocal-rank fusion (`rag/fusion.py`), a 12-feature cross-encoder seam with a
  JSON artefact contract (`rag/rerank.py`) and `HybridKnowledgeRetriever`
  (`rag/hybrid.py`), selected by `RETRIEVER=baseline|hybrid|hybrid_rerank` behind the
  unchanged `Retriever` protocol.
- **`ScoredChunk`** in `rag/base.py`: a retrieved passage with the score that ranked
  it and the stage that produced it, so a threshold can be fitted and a failure
  diagnosed. `RetrievedChunk` — what the prompt sees — still carries no number.
- **A contrastive training stack in `ml/`**: four encoders behind one protocol,
  hard-negative mining over top-N near misses preferring same-tag passages, and
  `MultipleNegativesRankingLoss` implemented over a frozen feature matrix, with epoch
  selection on dev MRR.
- **Five pipeline stages, one make target each**: `retrieval-finetune`,
  `retrieval-tune` (dev-only fusion sweep), `retrieval-rerank`,
  `retrieval-calibrate` (the "no good answer" floors), `retrieval-eval` — plus
  `retrieval-pipeline` and a ~30 s `retrieval-smoke` whose report is titled SMOKE so
  a plumbing number cannot be quoted as a result. Every stage logs to MLflow with the
  resolved config, dataset refs, seed and split fingerprints.
- **A committed calibration artefact**: `backend/app/ml_artifacts/retrieval_v1/tuning.json`
  (fusion weight, two abstain floors, their provenance and the four split
  fingerprints), with package-data so it ships in a wheel.
- [ADR 0035](docs/adr/0035-fine-tuned-retrieval.md),
  [docs/verification/day35-retrieval.md](docs/verification/day35-retrieval.md),
  `ml/reports/day35-retrieval.md`, model card v1.3 (§5.5 plus failure modes F14/F15),
  `ml/notebooks/colab_retrieval_finetune.ipynb` (generated by
  `_build_retrieval_notebook.py`), and four `ml/configs/retrieval_*.yaml`.
- **138 new ml tests** (30 data, 27 metrics, 47 training, 34 pipeline), nine new
  `dvc.lock` consistency tests (14 in the file), and **four new backend test files** (bm25, fusion, rerank, hybrid) plus
  `TestTheShippedCalibration` in the factory suite.

### Changed — Day 35

- `RETRIEVER` and its overrides (`RAG_HYBRID_DENSE_WEIGHT`, `RAG_HYBRID_FUSION_K`,
  `RAG_HYBRID_CANDIDATES`, `RAG_RERANK_CANDIDATES`, `RAG_ABSTAIN_SCORE`,
  `RAG_RERANKER_DIR`) documented in `.env.example`; `Settings` validates the choice
  and the overrides at startup.
- The hybrid settings abstain on a **calibrated score floor** rather than
  `RAG_MIN_SIMILARITY`: a cosine from one embedding space is not comparable to a rank
  two systems agreed on. `RAG_MIN_SIMILARITY` still applies to `baseline`.
- `dense_scale` is derived at build time as 2 × the served embedder's floor and is
  deliberately not persisted, so a stale number in a JSON file cannot override the
  live one.
- The Day-13 retriever grew the same `rank()` the hybrid has, so both settings are
  measured identically; `rag/embedders.py` shares one `tokenize()` (including the
  Indic combining-mark class) with the ml side.
- README (retrieval as a measurable feature), `backend/README.md` (a retrieval
  section and the `RETRIEVER` table), `ml/README.md` and `ml/notebooks/README.md`.

### Fixed — Day 35

- `install_backend` in `ml/retrieval/tuning.py` resolved the repository root one
  directory too high and wrote `backend/app/ml_artifacts/retrieval_v1/tuning.json`
  *next to* the checkout. It printed a path, so it looked like it worked; the
  destination is now a testable helper (`backend_artifact_dir`).
- `build_encoder` honoured `artifact_dir` before `kind`, so a `kind: tfidf` config
  with a stray artefact directory silently loaded trained projection weights and
  failed two stages later, far from the cause. It now applies only to
  `kind: projection`, the config validator refuses the combination, and the harness
  clears an inherited override.
- Three factory tests that pointed `RAG_RERANKER_DIR` at a non-existent path were
  silently reading the packaged artefact directory; "no tuning here" now means an
  existing empty directory (`no_artifacts()`), and the coupling to the committed
  calibration is pinned in one place instead.
- `load_cross_encoder` returns the concrete `LexicalCrossEncoder` so callers can read
  `.metadata` — loading *is* provenance — without casting away the protocol.
- The root `Makefile` had a duplicated `ml-smoke` target ("overriding recipe"
  warning).
- Two defects behind the same red CI step (`ml-smoke` 5, `manovia_ml.data.dvc_lock`),
  which skipped every later step both times:
  - **The committed `ml/dvc.lock` was stale** — it recorded the md5 of
    `src/manovia_ml/retrieval/data.py` from *before* `eval_set_ref` was added, so the
    lock described a module the commit did not contain. Regenerated with
    `make -C ml data` (four lines); the splits came back byte-identical, so no measured
    number changed. Same class as Day 31's `toy.py`, caught by the gate Day 31 added.
    `ml/tests/test_dvc_lock.py` now re-hashes every plain-file dependency in the
    committed lock and requires each `dvc.yaml` stage to have a lock entry, so this
    drift fails `make -C ml test` in ~0.1 s instead of CI in three minutes.
  - **The gate could not tell an unbuilt upstream output from drift.** Day 35 added the
    pipeline's first *chained* stages — `retrieval_split` consumes the gitignored
    `data/retrieval/expanded.jsonl` that `retrieval_expand` produces — so on a fresh
    clone, which is what CI checks out, that dependency is legitimately absent and the
    check failed with `retrieval_split: data/retrieval/expanded.jsonl (deleted)`. That
    would have failed every clone of every future branch. `pipeline_outputs()` now reads
    the `outs` of every stage in `dvc.yaml` (from the pipeline, not from the lock, which
    is the artefact under test) and `changed_deps` exempts a dependency in that set
    **only while its state is `deleted`** — one that exists but differs still fails, so
    the exemption is not a hole — and the CLI prints what it tolerated. Verified in four
    states by hand, under nine tests, and by a full `git clone` rehearsal that rebuilt
    both stages and reproduced every fingerprint byte-identically.

## [1.0.0] — 2026-10-13

The first release: everything from Days 1–30 below. `v1.0.0` is tagged on
`main` after the Day 30 pull request merges (the launch steps are in
[docs/releases/v1.0.0.md](docs/releases/v1.0.0.md), which is also the release
notes: features, safety statement, evaluation numbers, limitations).

### Added — Day 30: launch, positioning and career material

- A visible **Beta** badge in the app header and a **"Portfolio demo — not a
  clinical service"** line in the app footer (en/hi/bn) and at the top of the
  README. Both stay until the [clinical review](docs/clinical-review.md) is
  complete.
- [docs/releases/v1.0.0.md](docs/releases/v1.0.0.md): the v1.0.0 release notes,
  and [docs/release-checklist-v1.0.0.md](docs/release-checklist-v1.0.0.md): the
  launch checklist with the real command results.
- [docs/roadmap.md](docs/roadmap.md): v2.0 (Days 31–49: deep-learning and
  futuristic tracks) and the post-v2.0 commitments (clinician review, validated
  translations, user research with ethics approval, push reminders, an
  accessibility audit by real users, a native mobile wrapper).
- Career material under `docs/career/` (not linked from the README).
- A social-preview image at `docs/assets/social-preview.png` (original design).
- The release workflow prefers curated notes from `docs/releases/<tag>.md`
  when that file exists, and falls back to generated notes.

### Changed — Day 30

- Version bumped to **1.0.0** in `backend/pyproject.toml`,
  `frontend/package.json`, the FastAPI `API_VERSION` and the Sentry release
  string; `docs/openapi.json` and `docs/api.md` regenerated.

### Added — Day 29: red-team and bug bash

- A scripted red-team suite (`evals/redteam/`): 150 YAML cases run against the
  real app offline, a two-user IDOR sweep (18 routes) and a 10-prompt
  full-pipeline jailbreak run. Baseline 82/140 → final **150/150**. Every fix
  carries a regression test; the full report is in
  [docs/redteam-report.md](docs/redteam-report.md) and the decisions in
  [ADR 0030](docs/adr/0030-red-team-suite-and-session-risk.md).

### Fixed — Day 29

- The rules engine stopped reading at 4,000 characters (windowed assessment
  now covers long texts); ~36 euphemism/slang/other-language rules; a session
  risk accumulator (slow multi-turn escalation); output-guard gaps (method and
  pill quantities, jailbreak and prompt-leak categories); an injection tripwire
  on memory, retrieval and the knowledge loader; access tokens now die with
  their refresh family; a per-user export budget.

## Day 28 — documentation, model card and portfolio polish

### Added

- `docs/model-card.md` rewritten as a full model card: every model in the
  product (safety classifier, emotion analyser, language-model providers),
  intended and out-of-scope use, training and evaluation data, metrics with
  their intervals, known failure modes, ethical limits and an update policy.
  Headline figures were checked against the evaluation reports, and the card states its limits. Corrections from that check are listed in the Day 28 verification note.
- `docs/clinical-review.md`: the checklist a licensed professional should
  complete before public launch (crisis copy, helplines, knowledge base,
  exercise instructions, screening presentation and escalation policy). It has
  no sign-offs.
- `docs/api.md` and `docs/openapi.json`, generated from the FastAPI application
  by `backend/scripts/export_openapi.py`. `backend/tests/unit/test_api_docs_export.py`
  fails if the committed copies drift ([ADR 0027](docs/adr/0027-generated-api-reference.md)).
- `docs/architecture.md`: sequence diagrams for a chat turn and a crisis turn,
  including the offline "Need help now" path.
- Architecture decision records [0027](docs/adr/0027-generated-api-reference.md),
  [0028](docs/adr/0028-model-card-and-clinical-review-gate.md) and
  [0029](docs/adr/0029-branch-coverage-gates.md), and an index at
  [docs/adr/README.md](docs/adr/README.md) that flags the missing 0016.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue forms under
  `.github/ISSUE_TEMPLATE/` and a pull request template.
- `docs/demo-script.md`: a 90-second demo script. It has not been recorded.
- `docs/verification/day28-documentation-and-portfolio.md` (the Day 28 checks) and
  `docs/verification/day28-manual-tests.md` (what the sandbox could not run).

### Changed

- `README.md` rewritten: a plain statement of what the product is and is not, a
  three-command quick start, a crisis and emergency section, quality figures with
  the gate status, privacy, a documentation index, known limitations and a roadmap.
- `backend/app/api/v1/health.py`: the health router is tagged `health`, so it no
  longer appears under "Other" in the API reference. This is the only
  application-code change on Day 28.
- `docs/privacy-and-threat-model.md`: helplines described as schema-checked, not
  call-verified.
- **Removed `GET /api/v1/chat/sessions/{id}/stream?message=`.** It put the user's
  message in the URL, where proxies and access logs record it. The web client has
  always used the POST stream. The API reference now lists 62 operations.
- Two safety tests for branches the coverage gate found untested: the ensemble
  with a disabled classifier, and a pattern file whose top level is not a mapping.

### Fixed

- The model card claimed OpenAI was a supported provider. It is not: the
  configuration accepts `anthropic`, `ollama` and `fake` only.
- The README's static "backend coverage 100%" badge did not match the measured
  coverage. It now shows the measured statement and branch figures from the
  Day 28 run, with the gate result stated.
- `docs/evaluation-report.md` and the README called the Indian helplines
  "verified". `in-kiran` is flagged `needs_verification`; the wording now says so.
- Prettier formatting in 17 frontend files and the frontend README (line wrapping only).
- The safety branch coverage gate: 91.67% against 95% on the Day 28 check. It now passes
  with every safety module at 100% branches, after the two tests above.

## Day 27 — DevOps, observability and deployment

- Gunicorn process model with `UvicornWorker` processes; access log off for
  privacy; `GUNICORN_WORKERS=1` until the session store is shared.
- `docker-compose.prod.yml`: one-shot migration service; refuses to start
  without real `SECRET_KEY` and `FIELD_ENCRYPTION_KEY`.
- Prometheus metrics at `/metrics`, with closed-set labels only.
- Sentry with a privacy scrubber; opt-in by DSN.
- Feature flags; nightly backup and restore tooling, drilled against PostgreSQL 16.2.
- Fly.io (API and Postgres) and Netlify (web) deployment. See
  [ADR 0026](docs/adr/0026-deployment-platform.md) and
  [docs/deploy.md](docs/deploy.md).
- Verification: [docs/verification/day27-devops-observability-deployment.md](docs/verification/day27-devops-observability-deployment.md).

## Day 26 — Coverage gates

- Branch coverage measured; overall backend gate at 85%, every
  `app/services/safety` module at 95%, crypto and scoring at 100%
  (`backend/scripts/coverage_gate.py`, run by `make test`).
- **Status on Day 28:** the safety group measured 91.67% and failed its 95% floor
  until two tests were added; it passes now. CI does not run the branch gate yet.
  See [ADR 0029](docs/adr/0029-branch-coverage-gates.md).

## Day 25 — Offline, accessibility and low bandwidth (PWA)

- Installable PWA; offline "Need help now?" and breathing exercises; offline
  check-in outbox; WCAG 2.2 AA work and an in-app accessibility statement;
  bundle and Lighthouse budgets.
- [ADR 0025](docs/adr/0025-pwa-accessibility-low-bandwidth.md) ·
  [verification](docs/verification/day25-pwa-accessibility-low-bandwidth.md).

## Day 24 — Evaluation harness

- `make eval`: safety, emotion and response suites with bootstrap confidence
  intervals and CI gates (`evals/thresholds.yaml`); reports under `evals/reports/`.
- [ADR 0024](docs/adr/0024-evaluation-harness.md) ·
  [evaluation report](docs/evaluation-report.md) ·
  [verification](docs/verification/day24-evaluation-harness.md).

## Day 23 — English, Hindi and Bengali

- Interface and crisis copy in English, Hindi and Bengali; language-filtered
  retrieval; translation status ledger ([translations/STATUS.md](translations/STATUS.md)).
- [ADR 0023](docs/adr/0023-multilingual-english-hindi-bengali.md) ·
  [verification](docs/verification/day23-multilingual-english-hindi-bengali.md).

## Day 22 — Privacy controls and security hardening

- Export everything, delete everything, retention settings, refresh-token
  rotation and lockout, OriginGuard for unsafe methods.
- [ADR 0022](docs/adr/0022-privacy-controls-and-data-export.md) ·
  [threat model](docs/privacy-and-threat-model.md) ·
  [verification](docs/verification/day22-privacy-controls-and-security-hardening.md).

## Day 21 — Daily plan and Week-3 review

- Rule-based daily plan with a "why this?" for each item; quiet hours; support-only
  mode after a positive PHQ-9 item 9.
- [ADR 0021](docs/adr/0021-daily-plan-and-week-3-review.md) ·
  [verification](docs/verification/day21-daily-plan-week-3-review.md).

## Day 20 — PHQ-9 and GAD-7 screenings

- The official instrument wording and published scoring; support shown before
  any score when PHQ-9 item 9 is positive.
- [ADR 0020](docs/adr/0020-phq-9-and-gad-7-screenings.md) · [model card](docs/model-card.md).

## Day 19 — CBT thought record

- Seven-step, owner-only, encrypted thought record with per-step safety screening.
- [ADR 0019](docs/adr/0019-cbt-thought-record.md) ·
  [verification](docs/verification/day19-cbt-thought-record.md).

## Day 18 — Guided exercises

- Eight declarative exercises with a keyboard-operable runner.
- [ADR 0018](docs/adr/0018-guided-exercises.md) ·
  [verification](docs/verification/day18-guided-exercises.md).

## Day 17 — Encrypted journal

- AES-256-GCM envelope encryption with per-entry keys; client-side search.
- [ADR 0017](docs/adr/0017-encrypted-journal-and-envelope-encryption.md).

## Day 15 — Mood check-ins

- Five-point valence and energy check-ins, tags, streaks, encrypted notes.
- [ADR 0015](docs/adr/0015-mood-check-ins.md).

## Day 14 — Output guard and saved-chat memory

- Deterministic whole-reply output guard with one strict regeneration; encrypted,
  user-controlled saved-chat memory.
- [ADR 0014](docs/adr/0014-output-guard-and-conversation-memory.md).

## Day 13 — Knowledge base and retrieval

- Curated self-help knowledge base; retrieval filtered by language.
- [ADR 0013](docs/adr/0013-knowledge-retrieval-rag.md).

## Day 12 — Chat interface

- Streaming chat UI, crisis card, light and dark themes.
- [ADR 0012](docs/adr/0012-chat-ui.md).

## Day 11 — Chat orchestrator

- One pipeline for every message; crisis gate before any model call.
- [ADR 0011](docs/adr/0011-chat-orchestrator.md).

## Day 10 — LLM provider layer

- One provider interface; a fallback chain that ends in a canned local reply.
- [ADR 0010](docs/adr/0010-llm-abstraction.md).

## Day 9 — Safety ML ensemble

- A one-way ML backstop next to the rules engine; `make eval` safety suite.
- [ADR 0009](docs/adr/0009-safety-ml-ensemble.md).

## Day 8 — Crisis rules engine

- Deterministic, pattern-based crisis detection with normalisation and pragmatics.
- [ADR 0008](docs/adr/0008-crisis-detection-rules-engine.md) ·
  [safety design](docs/safety-design.md).

## Days 1–7 — Foundations

- Monorepo, backend and frontend skeletons, data layer with Alembic, guest
  authentication with consent gating, the emotion model, CI and the Docker stack.
- ADRs [0001](docs/adr/0001-monorepo-and-stack.md) to
  [0007](docs/adr/0007-ci-and-docker.md).
