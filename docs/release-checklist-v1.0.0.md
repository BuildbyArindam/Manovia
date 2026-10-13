# Release checklist — v1.0.0 (2026-10-13)

Every line was actually run on 2026-10-13 in the Day 30 sandbox; where the
sandbox could not do something, that is stated and the owner's step is spelled
out. Full command output summaries:
[docs/verification/day30-launch.md](verification/day30-launch.md).

## 1. Gates

| # | Item | Result | Evidence |
| --- | --- | --- | --- |
| 1 | All CI green | **PASS** | Latest `main` push (merge of PR #36): CI run `38368319917` — Backend (ruff, mypy, pytest), Frontend (lint, typecheck, vitest, build), Dependency audit, Secrets scan (gitleaks) all `success`; Accessibility & Lighthouse run `38368319863` `success`; Evaluation Harness green on PR #36. |
| 2 | `make eval` thresholds met | **PASS** | Run on the release tree: overall CI gate **PASS** — safety recall 1.000 (ensemble) / 0.333 (rules-only, floor 0.330), emotion macro-F1 0.804 (floor 0.550), response 60/60 (floor 1.000), 0 regressions. Reports: `evals/reports/2026-10-13/`. |
| 3 | Dependencies audited | **PASS** | `pip-audit` on the installed backend environment: **No known vulnerabilities found**. `npm audit --omit=dev --audit-level=high`: exit 0; 2 **moderate** advisories remain in `react-router` 6.x (GHSA-wrjc-x8rr-h8h6, GHSA-337j-9hxr-rhxg; only fix is the breaking 7.x upgrade) — below the gate, tracked as a known issue since Day 22. |
| 4 | Secrets scan clean | **PASS** | Offline stand-in `scripts/scan_secrets.py` (gitleaks rule set): 42 working-tree hits, **all `generic-api-key` matches on ordinary source prose** (storage-key constants, `credentials: "same-origin"`), same class as the Day 22 triage; a high-signal grep (AKIA…, PEM keys, `sk-ant-…`, `ghp_…`) matched nothing. The authoritative gitleaks job is green in CI (gate 1). |
| 5 | Helplines re-verified | **PARTIAL — human step** | All 35 entries carry `last_verified: 2026-10-09` (4 days old, within 30 days). The sandbox has **no outbound access to helpline sources**, so the dates were **not** bumped — re-dating without checking would fabricate verification. Four entries remain `needs_verification` (`in-icall`, `in-kiran`, `in-aasra`, `au-kids-helpline`). **Owner: phone-verify before anyone in distress relies on the file** (AGENTS.md rule 6). |
| 6 | Fresh-clone quick start works | **PASS (after a real fix)** | Brand-new clone in a temp dir → `cp .env.example .env` → migrate → API + built web → `scripts/smoke.sh` (skip-compose mode): **SMOKE PASS**. Found and fixed during this check: blank `.env` values (e.g. `RAG_RETRIEVAL_LIMIT=`) failed Settings validation, so a fresh clone could not boot — fixed with `env_ignore_empty` + two regression tests. Docker itself is unavailable in the sandbox; `docker compose up -d --build` must be rechecked by the owner on a Docker machine. |
| 7 | Changelog written | **PASS** | `CHANGELOG.md` has the `[1.0.0] — 2026-10-13` section (Days 29–30 detailed, Days 1–28 below it). |
| 8 | Version bumped | **PASS** | `1.0.0` in `backend/pyproject.toml`, `frontend/package.json` (+lockfile), FastAPI `API_VERSION`, Sentry release string; `docs/openapi.json` / `docs/api.md` regenerated (drift test passes). |
| 9 | Tag v1.0.0 + GitHub Release | **PREPARED — owner publishes** | This session cannot push tags (it is pinned to its working branch) and the release must point at the merged `main` commit anyway. Notes are committed at `docs/releases/v1.0.0.md`; the release workflow now uses them automatically. Exact steps below. |

## 2. Publishing the release (owner, after the Day 30 PR merges)

```bash
git checkout main && git pull
git tag -a v1.0.0 -m "Manovia v1.0.0"
git push origin v1.0.0
```

That single tag push triggers `.github/workflows/release.yml`, which (in
order) round-trips migrations on Postgres 16, builds and pushes both images
to GHCR, and creates the GitHub Release **with the curated notes from
`docs/releases/v1.0.0.md`** (features, safety statement, evaluation numbers,
limitations). No other step is needed. If you prefer to create the release
by hand instead: `gh release create v1.0.0 --title "Manovia v1.0.0"
--notes-file docs/releases/v1.0.0.md --verify-tag` (then the workflow's
release step will no-op/fail on the duplicate — tag-push alone is the
supported path).

## 3. GitHub polish (owner — the sandbox token got HTTP 403 on repo settings)

Attempted from the sandbox: `gh repo edit` → `HTTP 403: Resource not
accessible by integration`. Run these with your own credentials:

```bash
gh repo edit BuildbyArindam/Manovia \
  --description "Manovia — privacy-first, safety-first mental wellbeing self-help companion (beta portfolio demo, not a clinical service). Rules-first crisis detection before any LLM call; en/hi/bn; offline-capable PWA." \
  --enable-discussions \
  --add-topic mental-health --add-topic fastapi --add-topic react \
  --add-topic typescript --add-topic llm-safety --add-topic privacy \
  --add-topic pwa --add-topic cbt --add-topic evaluation-harness \
  --add-topic red-teaming --add-topic python --add-topic self-help
```

In the web UI (no API exists for these):

1. **Pin the repository**: profile → Customize your pins → Manovia.
2. **Social preview**: repo Settings → General → Social preview → upload
   `docs/assets/social-preview.png` (original design, 1280×640, committed).
3. **Issue templates**: already shipped under `.github/ISSUE_TEMPLATE/`
   (bug report, feature request, safety concern + config) — nothing to do.

## 4. Positioning (shipped in this release)

- App header shows a **Beta** badge; app footer shows **"Portfolio demo —
  not a clinical service. Clinical review is not complete."** in en/hi/bn,
  on every page (regression-tested in `frontend/src/App.test.tsx`).
- README opens with the same statement. Both stay until
  [docs/clinical-review.md](clinical-review.md) is signed off.

## 5. Before real users could rely on this (human, non-negotiable)

1. **Clinical review** by a licensed professional — the checklist is
   `docs/clinical-review.md`; it has no sign-offs.
2. **Helpline phone verification** — every number in
   `backend/app/content/helplines.json`, especially the four flagged entries.
3. **Native-speaker review** of all Hindi/Bengali strings and the
   non-English crisis rules (`translations/STATUS.md`).
4. **Legal/privacy review for the operating jurisdiction** (DPDP Act in
   India / GDPR if EU users; age policy — the product currently has none).
5. **A real deployment** per `docs/deploy.md`, plus the Docker-based quick
   start re-checked on a Docker machine.
6. **Real-provider run** of the jailbreak suite with an actual API key
   (`evals/redteam/jailbreak_live.py --real`).
