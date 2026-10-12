# Day 22 — Privacy controls and security hardening: verification

Date: **2026-10-12**. Work is on `arena/a81666f5-manovia` (the session-pinned
branch; the requested `day-22-privacy-controls-security-hardening` branch could
not be created in this session). Nothing was pushed to `main`. **No PR was
merged.**

This document is the record of Day 22's *verification* pass. Message 1 built the
feature; everything below was run afterwards, against the built state, and the
numbers are the ones the commands printed. Where a check failed, the failure and
its root-cause fix are recorded rather than quietly re-run — a verification
document that only shows green is a marketing artefact.

## Environment and scope

- Python 3.11.2 with backend requirements pinned to exact versions
  (`backend/pyproject.toml`, Day 22), Node 22.22.3, `npm ci` from the committed
  lockfile.
- Real FastAPI on `0.0.0.0:8000` against throwaway SQLite databases migrated
  fresh through `0008` (`/tmp/day22_verify.db`, `/tmp/day22_retention.db`),
  `LLM_PROVIDER=fake` with `LLM_FAKE_LATENCY_MS=400`, rules-only safety
  (`SAFETY_ML_ENABLED=false`), `EMOTION_ANALYZER=keyword`. The serial e2e shares
  one client IP, so its rate limits were raised for the test server only
  (`RATE_LIMIT_AUTH_PER_MINUTE=300`, `RATE_LIMIT_GLOBAL_PER_MINUTE=600`; shipping
  defaults 10 and 120 unchanged).
- Vite on `0.0.0.0:5173` (same-origin `/api` proxy) for the e2e suite.
- Real Chromium **153.0.8010.0** from npm's `@sparticuz/chromium@153.0.0`
  (`cdn.playwright.dev` is not reachable from this sandbox); the bundled AL2023
  shared libraries were inflated and used via `LD_LIBRARY_PATH`. Browser web
  security was **not** disabled.
- **No Docker.** The compose stack and nginx itself were not exercised; the
  production header file was verified by serving `dist/` with those exact
  headers from a local server (see §6).
- Verification added no product features. It found one real product defect
  (§8), two environment/dependency defects (§5, §8) and several bugs in the
  throwaway probe scripts, which were fixed in the probes.

## PASS / FAIL table

| # | Check | Result | Actual evidence |
| --- | --- | --- | --- |
| 1 | Export flow: create data in every feature, call `GET /privacy/export`, list files, confirm decrypted | **PASS** | ZIP with **13 entries** (`README.txt`, `manifest.json`, and 11 data files); `format_version=manovia-export-1`, `contents_are_decrypted=true`, `truncated={}`, `unreadable_rows={messages:0, journal_entries:0, cbt_records:0}`; counts `consents 4, chat_sessions 1, messages 2, journal_entries 1, mood_entries 1, assessment_results 1, exercise_completions 1, cbt_records 1, plan_items 1`; **6/6** sentinel strings written through the API appear **decrypted** in the archive; `?format=json` returns the same files |
| 2 | Delete flow: delete the account, query every table by `user_id`, confirm old tokens fail | **PASS** | before: 15 rows across 11 tables; wrong confirmation → **400** `confirmation_required`; correct → **200** with `{irreversible, rows_deleted, total_rows: 15, receipt_id, account_ref}`; after: **0 rows in all 14 user-scoped tables** (queried directly in SQLite, ids normalised to the stored 32-char hex); receipt row survives with columns exactly `{id, account_ref, requested_via, rows_deleted, created_at}`; old access token → **401 `account_deleted`** on `/auth/me` and `/mood`; old refresh token → **401**; export with the old token → **401** |
| 3 | Sentinel-log test, sentinels printed | **PASS** | `pytest tests/security/test_log_sentinels.py` → **7 passed**. Sentinels: `SENTINEL-CHAT-7f3a91c4-do-not-log`, `SENTINEL-CHAT-REPLY-2b8e40`, `SENTINEL-JOURNAL-TITLE-5c1d`, `SENTINEL-JOURNAL-BODY-88ae2f`, `SENTINEL-MOOD-NOTE-1de77b`, `SENTINEL-CBT-4a9c02`, `SENTINEL-EXPORT-6fd3`. Independently, five fresh sentinels were pushed through the **live** API and the server's own log file grepped: **0 occurrences**, while `chat_turn`, `journal.created`, `mood_check_in_recorded` and `privacy.export` were all logged (the check is not vacuous) |
| 4 | XSS: `<img src=x onerror=...>` in chat, journal and mood, asserted rendered as text in a real browser | **PASS** | `e2e/xss.spec.ts` in real Chromium at 360x640: **5 passed (29.4s)** — each surface asserts the payload is a **text node** (`toHaveText(XSS)`), that `img[src="x"]` and `svg[onload]` counts are **0**, and that the payload's global `window.__manoviaXss` is **not** set; plus zero dev-CSP violations walking `/settings /journal /chat /mood`, and the Privacy screen reachable. Whole suite: **52 passed (4.4m)** at both viewports |
| 5 | `pip-audit`, `npm audit --omit=dev`, gitleaks (+ fixes) | **PASS after a fix** | `pip-audit`: first run flagged **`setuptools 66.1.1` — PYSEC-2026-3447** (the version `python -m venv` seeded; the new gate correctly refused it) → **fixed** by pinning `setuptools==83.0.0` in the dev extra and the build backend, re-run **clean**. `npm audit --omit=dev`: **2 moderate** advisories in `react-router` (via `react-router-dom`, no high/critical; only fix is a breaking 7.x upgrade) → the gate is `--audit-level=high`, which **exits 0** and reports the moderates in the job summary; tracked as a known issue. Secrets: `scripts/scan_secrets.py` (gitleaks' own 217-rule set) reports **37** working-tree and **44** history hits, **all `generic-api-key` on ordinary source prose** (`queryKey: moodKeys.streak`, `password_hash`, `TokenType`); a separate high-signal grep (`AKIA…`, PEM keys, `sk-ant-…`, `ghp_…`) matched nothing outside the vendored gitleaks rule file, where the matches are that file's *own* allowlist examples of fake Google keys (rule file verified byte-identical to upstream v8.28.0, sha256 `c3bee40d87bb4689e3f5ffa5cc0315f7e6e4d43f4e9388397fca59f29407b432`) |
| 6 | Security headers via `curl -I`, API and web | **PASS (web: see caveat)** | **API** (`/api/v1/health`): `x-content-type-options: nosniff`, `x-frame-options: DENY`, `referrer-policy: no-referrer`, `content-security-policy: default-src 'none'; frame-ancestors 'none'`, `cache-control: no-store`, `permissions-policy: camera=(), microphone=(), geolocation=(), payment=()`, `x-permitted-cross-domain-policies: none`. **Web dev server**: the documented `DEV_CSP` + nosniff + no-referrer. **Web production headers** (read out of `docker/security-headers.conf`, applied to the real `dist/`): all six headers present, and five routes load in Chromium with **0 CSP violations**; the negative control (`script-src 'none'`) is detected and exits 1. *Caveat: nginx itself was not run (no Docker), so `docker/nginx.conf` wiring — including the `location /assets/` re-include — is reviewed, not executed* |
| 7 | Three biggest remaining privacy risks, honestly | **PASS** | Recorded in `docs/privacy-and-threat-model.md` §6.3 and below |
| 8 | Backend suite (full) | **PASS** | **3609 passed, 3 skipped**, coverage **95.47%** (`--cov-fail-under=80` satisfied) with the **pinned** dependency set |
| 9 | Frontend suite (full) | **PASS** | **474 passed, 3 skipped** across 54 files (the 3 skips are live-backend tests) |
| 10 | Lint / format / types | **PASS** | backend `ruff check` + `ruff format --check` clean (314 files), `mypy app tests` **Success: 261 files**; frontend ESLint, `tsc --noEmit`, Prettier all clean |
| 11 | Migrations on a fresh database | **PASS** | `alembic upgrade head` ran `0001`→`0008` twice on fresh SQLite files during this pass |
| 12 | Retention job, for real | **PASS** | Seeded one user with `retention_days=30` and check-ins aged 1, 3, 40, 45 days. **Dry run**: "2 rows would go / mood_entries: 2". **Real run**: "rows deleted: 2". **Re-run**: "rows deleted: 0" (idempotent). Remaining rows are the 1- and 3-day-old check-ins |
| 13 | `pytest tests/security tests/privacy` | **PASS** | **144 passed** (injection 81, log sentinels 7, middleware guards 12, privacy 43+) |

## 8. Defects found during verification, and what fixed them

### 8.1 A real product bug: thought records exported as `null`

**Symptom.** Live export of an account with one CBT record produced
`"unreadable": {"cbt_records": 1}` and a `cbt.json` full of `null` fields. The
export's own manifest reported it — which is the mechanism working — but a
user's thought record was silently unreadable in their export.

**Root cause.** The write path binds AES-GCM Associated Data
`cbt.<field>` (`app/api/v1/cbt.py`); the export read it back under the bare
`<field>` (`app/services/privacy/export.py`). Different AAD, so decryption
fails. The existing export test did not catch it because **its fixture built
`CBTRecord` rows itself, using the export's spelling** — the fixture and the
export agreed with each other and disagreed with the code that writes a real
user's row. This is the classic "the double reimplements the contract" failure.

**Fix.** The AAD now has one definition, `cbt_aad(field)` in
`app/models/cbt.py`, used by the write path, the read path and the export; the
export test's fixture was repointed at it; and a **new regression test goes
through the real endpoint** —
`test_a_thought_record_saved_through_the_api_exports_decrypted` posts a record
to `POST /api/v1/cbt/records` and then exports it. Verified live afterwards:
**6/6 sentinels decrypted, `unreadable_rows.cbt_records: 0`**.

**Negative control.** With the export fix reverted in place, the new test
**fails** (`unreadable: {"cbt_records": 1}` in the captured log); with it
restored, it passes. The test can actually detect the bug it was written for.

### 8.2 The new dependency gate failed on the environment's own `setuptools`

**Symptom.** The first `pip-audit` run under the new blocking gate reported
`setuptools 66.1.1` / PYSEC-2026-3447.

**Root cause.** `python -m venv` seeds whatever setuptools the distribution
ships; nothing in the project upgraded it, and `pip-audit` audits the whole
environment, not just the declared dependencies.

**Fix.** `setuptools==83.0.0` is now pinned in the dev extra (which is what CI
installs) and in `[build-system].requires`. Re-run is clean. The alternative —
`--ignore-vuln` — was rejected: the advisory was real and trivially fixable, and
a gate that starts with an exception list stops being a gate.

### 8.3 A flaky e2e assertion, fixed at the source (earlier in the day)

The chat spec asserted the "Manovia is typing" indicator, which the Fake provider
made true for ~20 ms. Reproduced on the pre-Day-22 commit in a clean worktree, so
it was not caused by this day's work. Fixed by adding `LLM_FAKE_LATENCY_MS`
(default 0, so unit tests stay instant) so an e2e run can hold a transient state
long enough to observe it — rather than weakening the assertion or adding a retry.

## 9. The three biggest remaining privacy risks

Restated here so they travel with the verification rather than only in the
threat model.

1. **Refresh tokens live in `localStorage`.** An XSS that gets through would
   escalate to account persistence. The defence is layered (no
   `dangerouslySetInnerHTML` anywhere, a strict CSP with no inline script, and
   the XSS spec passing) but it is defence in depth, not immunity. The fix is an
   `HttpOnly` + `SameSite=Strict` refresh cookie with CSRF protection — a
   cross-cutting auth change, out of scope today. **Highest-value change left.**
2. **SQLite, single process, is the only configuration exercised.** Rate
   limiting, lockout counters and the ephemeral chat store are per process, so
   the effective limits multiply with worker count. PostgreSQL is supported by
   the code and migrations and **has never been run here** (no Docker).
3. **No clinician, native speaker or crisis-line operator has reviewed the
   safety copy or dialled a helpline.** Rules recall is 1.000 on the dev split
   and the helpline file is schema-validated at import, but a wrong number on a
   crisis card is the worst failure this app can have, and no test can rule it
   out. This needs a human, not more code.

Adjacent, lower-severity: the safety ML still over-escalates on exam phrases;
there is no purge job turning the pre-Day-22 soft-delete column `deleted_at`
into a real deletion (accounts deleted through `DELETE /privacy/account` are
hard-deleted in full); and two moderate `react-router` advisories remain
(no high/critical, only fix is a breaking upgrade).

## 10. What must be checked by hand

1. `docker compose up` — the compose stack, nginx wiring and PostgreSQL have not
   been exercised in this sandbox (no docker binary).
2. Repeat the deletion and retention runs against **PostgreSQL**: the cascade is
   native there and PRAGMA-driven on SQLite.
3. Point the app at a **real LLM provider**; everything here ran against
   `LLM_PROVIDER=fake`.
4. Native-speaker and clinical review of the Hindi/Bengali crisis copy, and
   dialling every number in `backend/app/content/helplines.json`.
5. Put the retention CLI behind a real scheduler; it works, but nothing invokes
   it on a timer.

## 11. Scope note: what Day 22 did not do

- No real E2EE, no client-side key management.
- No purge job for pre-existing `deleted_at` rows.
- No cookie-based session migration (see risk 1).
- No shared/distributed rate limiter.
- No third-party security review or penetration test.
