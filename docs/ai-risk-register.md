# AI Risk Register — Manovia (Day 48)

> Live risk inventory for every AI/ML component that touches a person.
> Each risk has a likelihood, severity, owner and mitigation. High-severity
> risks have a tested gate (threshold, test or runbook step) — not just a note.

Reviewed: 2026-10-20. Owner: Manovia maintainers. Next review: weekly with drift report.

## Risk matrix

| Likelihood | Severity | Meaning |
| --- | --- | --- |
| Low | the case is possible but not expected in normal use |
| Medium | happens sometimes in normal traffic |
| High | expected without mitigation |
| Low severity | inconvenience, wrong tone |
| Medium | wrong card, unnecessary check-in |
| High | crisis missed or false clinical claim |
| Critical | harm could follow from the error |

## Risks

| # | Risk | Component | Likelihood | Severity | Owner | Mitigation | Status | Gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R1 | **Missed crisis** — rules + safety ML both miss HIGH/IMMINENT because phrasing is novel, indirect, or in low-resource language (hi-Latn, bn) | C1, C2 | Medium | **Critical** | Safety lead | Rules-first with ML backstop that can only raise; frozen test split enforces HIGH+IMMINENT recall ≥0.97 (ensemble) and ≥0.33 (rules-only); evals run weekly + on PR; 572-case corpus with hard negatives; human re-verification cadence | Open | `evals/thresholds.yaml: high_imminent_recall_min` |
| R2 | **Over-escalation** — benign exam/work stress flagged HIGH, causing crisis-card fatigue and LLM bypass of everyday distress | C1, C2 | High | **High** | Safety lead | Precision-leaning thresholds documented; retrained classifier planned (Day 27/29 notes); rules-only fallback (`SAFETY_ML_ENABLED=false`) still passes recall; user can still talk via crisis card helplines | Open | `release_gates.yaml: fairness max_gap` + eval report |
| R3 | **Bias across identity** — safety or emotion score changes when identity term swapped (religion, caste, gender, disability, sexuality, region, dialect) | C2, C4, C8 | Medium | **High** | ML lead | Templated paired-sentence tests across 17 templates; max-gap thresholds in `release_gates.yaml` (0.08 protected attrs, 0.15 global); CI fails when exceeded; report + fixes required | **Mitigated** | `ml/scripts/check_fairness_gates.py` gate |
| R4 | **Privacy leak via logging or metrics** — raw text or identifiers reach logs, Sentry, Prometheus labels, or drift aggregates | All | Medium | **Critical** | Backend lead | Never log raw text at INFO+ (tested); Sentry scrubber drops bodies/query/cookies; route-template only metrics; drift stores only histograms/centroids (no text, no embeddings); redacted_path for share tokens | Mitigated | `tests/unit/test_logging_redaction.py`, `test_metrics_labels` |
| R5 | **Hallucinated or unsafe LLM reply** — diagnosis, medication dosing, isolation, outcome promises, self-harm method language | C6, C7 | Medium | **High** | Safety lead | Output guard (YAML categories + length/language/missing check-in); one strict retry then safe local reply; guard block rate tracked; every medium+ reply checked | Mitigated | `output_guard` + evals/chat_rubric 60 scenarios |
| R6 | **Retrieval mis-grounding** — irrelevant or out-of-scope card shown, or abstain floor lets through a wrong answer / blocks a good one | C8 | High | **Medium** | Retrieval lead | Calibrated abstain floor per setting on 20-row calibration split (tuning.json + fingerprints); serve calibrated only; offline fallback is silence, not guess | Mitigated | `ml/configs/release_gates.yaml` + retrieval report |
| R7 | **Drift — language, length, confidence, safety rates shift** without alert, causing silent quality loss | All models | High | **High** | ML/Ops | Drift monitor tracks length, language mix, centroid shift (on-the-fly, aggregates only), confidence, safety rates, abstention/guard block; PSI/KS alerts via metrics; weekly drift report in `ml/reports/drift/` | **New Day 48** | `backend/scripts/weekly_drift_report.py` |
| R8 | **Voice injection** — spoken transcript contains jailbreak/tool instructions that bypass text safety | C7, C1 | Medium | **High** | Safety/voice | WebRTC VAD only for speech/silence (no emotion/health inference); STT words pass through ChatOrchestrator.admit()/stream() same as typed; no voice-only LLM route; crisis at HIGH still pre-written | Mitigated | `tests/unit/test_voice_safety.py` |
| R9 | **Agent tool injection** — directory/tool description contains poisoned instructions | C8, agent | Medium | **High** | Agent lead | Tool results are data (sanitised, envelope-marked, instruction-scanned); poisoned results quarantined wholesale + logged; strict JSON schemas; max 6 steps + 4000 token budget | Mitigated | `evals/redteam` + `tests/agent/test_injection_quarantine.py` |
| R10 | **Agent does what person did not ask** — set_reminder without consent | Agent | Low | **Medium** | Agent lead | set_reminder pauses for confirmation card (unguessable pending id, 15-min TTL); executes only on explicit allow; denials/expiries handled; action log | Mitigated | `tests/agent/test_confirmation_gating.py` |
| R11 | **Alarming forecast** — Outlook says crash to 1.0 or implies certainty, causing distress | Forecasting | Medium | **Medium** | Product/clinical | Off by default (explicit opt-in); range shown as variability not CI; copy says can be wrong/not medical; 14/28 + recency gates; kill switch | Mitigated | `tests/unit/test_forecast_copy.py` |
| R12 | **Bandit manipulation** — policy learns to maximise engagement rather than wellbeing (repeated suggestions) | Bandit | Medium | **Medium** | ML lead | Reward bounded pre/post rating + small abandonment penalty (no time-in-app, streak features); 20% exploration cap + no-consecutive-exploration wrapper; skips do not train; feature-derived why-this | Mitigated | `tests/policy/test_bandit_constraints.py` |
| R13 | **Private-mode parity drift** — browser rules diverge from server, giving different crisis levels | Private mode | Medium | **High** | Privacy lead | Compiled safety bundle from same sources; both sides pin hash; parity corpus 500/500 identical; drift.test.ts gates mismatch | Mitigated | `make parity` |
| R14 | **Private-mode model unverified** — WebLLM cache not hashed, or model not pinned | Private mode | High | **Medium** | Privacy lead | Manifest requires sha256 per model; pin script verifies upstream hash before writing; WebLLM marked managed_by_runtime (disclosed); unpinned = not installable (fail closed) | Open | `mcp-server`/manifest tests |
| R15 | **XR comfort** — flashing, sudden motion, locomotion, or long VR causes discomfort | XR | Medium | **Medium** | XR lead | 2D primary (no WebGL needed); WebXR local/seated only; one-press leave; fixed horizon; constant colours; zero-slope breath curve; 10-min break prompt; who-VR-not-for note | Mitigated | `e2e/calm.spec.ts` + `test_calm_comfort.py` |
| R16 | **Dependency / supply chain** — model or package compromised; training data licence wrong | All ML | Low | **High** | Ops | Licence catalogue with verified_on + evidence; DO NOT USE for unclear licences; .gitleaks scan; pip-audit; model ids never hard-coded; sha256 verification for ONNX | Mitigated | CI + catalogue tests |
| R17 | **Multilingual gaps** — Hindi/Bengali crisis copy or forecast copy is AI-draft, not native-reviewed | C1, C6, C7, C10 | High | **Medium** | Localisation lead | Ledger `translations/STATUS.md` marks every hi/bn group as needs-review; shipped copy flagged; pseudo-localisation catches missing keys; no claim of fluency | Open | `locales/parity.test.ts` |
| R18 | **Over-reliance / clinical misinterpretation** — person or clinician treats Manovia as diagnosis/EHR/triage | Product | Medium | **Critical** | Product/clinical | Model card explicitly out-of-scope; clinician-share bundle says Patient-generated, not a diagnosis on every page; Provenance author Patient; help text never promises outcomes | Mitigated | `docs/model-card.md` §3 |
| R19 | **Inability to turn off an AI feature** — operator cannot kill a harmful feature without deploy | All features | Low | **High** | Ops | Per-model + per-feature kill switches (voice, agent, forecasting, bandit, private, XR) toggleable at runtime via `/api/v1/feature-flags` without restart; each has tested safe fallback; metrics on fallback rate | **New Day 48** | `tests/test_kill_switches.py` |
| R20 | **Child safety / age gate** — no age policy; minors could use and be mis-screened | Product | Medium | **High** | Product/legal | Documented out-of-scope in model card (no minors until age gate); onboarding reminds not a service for minors; no personal-data collection beyond account | Open | Model card §3 |
| R21 | **Misuse of self-help data** — employer/school/insurer uses data to monitor others | Product | Low | **High** | Legal | Model card out-of-scope explicitly forbids monitoring others; export is user-controlled only; no bulk export; STRIDE review in privacy-and-threat-model.md | Mitigated | Model card §3 |

## Five highest-severity items (for verification)

| Rank | # | Risk | Likelihood×Severity | Why top |
| --- | --- | --- | --- | --- |
| 1 | R1 | Missed crisis | Medium×Critical | Immediate harm if missed; safety ordering is the core promise. Mitigated by ensemble + recall gates but corpus is still synthetic and single-annotator — the biggest residual. |
| 2 | R4 | Privacy leak via logging/metrics | Medium×Critical | A leak is irreversible and breaks the privacy-first contract. Mitigated structurally but depends on every caller respecting the rule — not just the guard. |
| 3 | R18 | Over-reliance / clinical misinterpretation | Medium×Critical | A clinician acting on a self-help document as if it were a medical record can cause downstream decisions. Mitigated by wording + Provenance + not-a-diagnosis banners, but importer behaviour is outside our control (52 validator warnings remain acknowledged). |
| 4 | R3 | Bias across identity | Medium×High | A model that treats a Muslim or Dalit person differently is a fairness failure even when recall is intact. New Day-48 gate makes it visible and blocking. |
| 5 | R7 | Drift without alert | High×High | Without PSI/KS monitoring, quality can decay silently (longer inputs, more Indic, lower confidence). Day-48 monitor + weekly report closes this gap; thresholds are still new and need tuning on live traffic. |

## Review cadence

- Re-run fairness audit on every model change (CI gate).
- Generate weekly drift report every Monday from production aggregates; alert owner on critical.
- Re-verify high-severity mitigations quarterly with clinical + accessibility reviewers.

## Parking lot (moved from PROGRESS.md)

- Pre-declared membership-inference sweeps with confidence intervals before user-level DP claims.
- Real-device WebGPU/WebXR headset measurements for tokens/s and comfort.
