# Day 19 verification — CBT thought record

Run 2026-10-11, branch `arena/ee253d5a-manovia` (Arena-pinned; cannot use the
requested branch name). All external model calls were Fake or deterministic;
no secrets were used. Browser checks used real Chromium, a migrated SQLite
backend, Vite's relative `/api` proxy and `LLM_PROVIDER=fake` with
`SAFETY_ML_ENABLED=false` (supported rules-only safety mode). The optional ML
model is separately evaluated below. No PR was merged.

## Real commands and results

| Check | Result | Evidence |
| --- | --- | --- |
| `make test` | **PASS** | 3061 backend passed / 3 optional skipped; 381 frontend passed / 3 skipped. `test_cbt_api.py` 5 tests; `CBTPage.test.tsx` 2 tests. |
| Schema/migration | **PASS** | `alembic upgrade head` applied `0006`; `alembic check`: `No new upgrade operations detected.` Focused seed, model, migration, rotation and CBT suite: 46 passed. |
| Fixed taxonomy, diagnosis-like output | **PASS** | Scripted Fake JSON with `made-up` yielded only `mind-reading`, `fortune-telling`, `labelling` (LLM path). Fake reply `["mind-reading"] You have a disorder.` (JSON syntax invalid) fell back to the same three IDs; `disorder` never appeared in response. Tests also check PII redaction and fixed `might be` explanations. |
| API wizard walkthrough | **PASS in rules-only mode** | Five `/screen` calls: 200, uninterrupted; `/traps`: 200 fallback `mind-reading`, `fortune-telling`, `labelling`; `/records`: 200 with anxiety 85 → 45; detail 200, list 1, delete 204. Scripted Fake path above produced the same three IDs. |
| Safety interrupt | **PASS** | HIGH at automatic-thought `/traps`: 200, `interrupted: true`, 3 resources and zero Fake calls. HIGH at another field also interrupted; bypassing wizard on `/records` saves no row. Frontend test confirms wizard stops mid-flow and offers “Continue later.” |
| Encryption at rest | **PASS** | Raw SQLite query after API walkthrough: all five free-text columns begin with `MENV\x01` and none contain corresponding UTF-8 text. Rotation regression: old key retired after re-encrypting five columns, then every value decrypts using only the new key. |
| Frontend keyboard-only e2e | **PASS in rules-only mode** | `playwright test e2e/cbt.spec.ts`: 2 passed (mobile 360×640 and desktop 1280×800). Every wizard action used Tab/Enter/Space/keyboard typing; observed `anxiety: before 85/100 → after 45/100` and `What helped: looking at evidence`. Onboarding/nav used mouse only to establish a session. |
| Final lint/type/build/format | **PASS** | `make lint`: Ruff, Ruff format and mypy 226 source files clean; ESLint clean. `npm --prefix frontend run typecheck`, `build` (755 modules) and `format:check` all passed. |
| Default optional safety ML on sample | **KNOWN ISSUE / NOT PASS** | Direct `run_ensemble` with default ML reported HIGH (`ml.crisis_mass`) on both `My exam is tomorrow` and `I'll fail the exam and everyone will think I'm stupid`; this was reproduced under scikit-learn 1.5.2 (model training version), so not merely a version mismatch. First browser attempt correctly stopped at the crisis card; **no threshold or test was weakened**. This is an existing classifier false-positive needing separate labelled safety review. |

Initial `make test` ran 3053 passed, 4 failed, 3 skipped: four inventory
assertions did not know about the new `cbt_records` table. Updated the exact
schema and seed-count inventories (historical demo has 0 CBT records) and added
CBT CRUD/encryption/ownership/key-rotation regression coverage. Focused rerun
46 passed; final full rerun passed as above. Initial browser attempt also
failed before navigation because the sandbox lacked Chromium's NSS libraries;
extracted the bundled `al2023.tar.br` libraries from the npm-hosted
`@sparticuz/chromium@141.0.0` package into `/tmp`, then ran real Chromium.
The following first browser run with default ML interrupted the exam sample,
which is the known issue above; the supported rules-only run passed 2/2.

## Known issues / hand checks

- With default ML enabled, review the exam-phrase HIGH false positive with a
  labelled safety dataset and clinical review **before** any threshold change.
  The wizard intentionally honours every HIGH decision.
- Verify copy and resource suitability in target regions and languages with
  qualified reviewers. Taxonomy explanations and fallback are English-only.
- Check screen-reader announcements and focus after the crisis interruption on
  actual devices; this run proves keyboard traversal in Chromium, not AT speech.
- Unfinished “Continue later” drafts live only in tab memory and vanish on reload;
  history lists the newest 100 records without pagination.
- `npm ci` reported 9 existing dependency advisories (4 moderate, 5 high);
  no dependency changes were made for this feature. The browser binary/npm
  package used only for local verification was installed with `--no-save` and
  `--package-lock=false`, not committed.
