# Day 23 verification — Multilingual (English, Hindi, Bengali)

Date: 2026-10-12 · Session branch: `arena/09204924-manovia`
Every command below was run in this environment; results are real.

## Automated results

| Check | Command | Result |
| --- | --- | --- |
| Backend full suite | `pytest backend/tests -q` | **PASS — 3646 passed, 3 skipped** (skips = optional extras only) |
| Backend safety suite | `pytest backend/tests/safety -q` | **PASS — 644 passed** |
| Backend chat suites | `pytest backend/tests/chat -q` | **PASS — 190 passed** (184 prior + 6 new locale tests) |
| Backend lint | `ruff check .` + `ruff format --check .` | **PASS** |
| Backend types | `mypy app tests` | **PASS — no issues in 264 files** |
| Safety eval (dev split) | `python evals/run_safety_eval.py` | **PASS — crisis recall 1.000, 0 false negatives** |
| Frontend tests | `npx vitest run` | **PASS — 489 passed, 3 skipped** (56 files) |
| Frontend types | `tsc --noEmit` | **PASS** |
| Frontend lint | `npm run lint` | **PASS** |
| Frontend build | `npm run build` | **PASS** |

## The Day-23 acceptance checks

| # | Check | Result | Evidence |
| --- | --- | --- | --- |
| 1 | **No hard-coded UI strings.** Bundle scan over `src/**` | **PASS — 0 candidates** | `node scripts/scan-ui-strings.mjs` → "Total: 0 candidate hard-coded strings in 0 files". Scanner blind spots (string props outside its attr list, arrays) were covered by a manual grep sweep of every feature module; its leftovers (chat empty state, journal decrypting line, exercise intensity options, printable-summary prefix, onboarding auth errors) were converted too. |
| 2 | **Locale parity.** en/hi/bn carry identical keys and placeholders | **PASS — 740 keys each** | `locales/parity.test.ts` (3 tests × 2 locales) — missing keys, orphan keys, placeholder mismatches and empty strings all fail the build. |
| 3 | **Pseudo-localisation mode works** (catches un-routed strings + overflow) | **PASS** | `pseudo.test.ts` — transform accents/expands/wraps; a full i18next instance in `pseudo` renders ⟦…⟧; real locales pass through untouched. The mode itself was found broken (post-processor registered but never activated) and fixed at the root (`postProcess` init option), now pinned by these tests. |
| 4 | **Language switcher + persisted preference** | **PASS** | switcher writes `manovia.language.v1`; resolution order stored → browser subtag → en (`i18n.ts`); re-render via provider revision bump. Covered by the i18n unit tests and the LanguageSwitcher component tests. |
| 5 | **Intl date/number formatting follows the language** | **PASS** | `lib/dates.ts` routes every `Intl` call through `intlLocale()`; the two components that formatted inline (MoodHistory, ExerciseHistory) now do too (grep-verified: no raw `toLocaleString()` / `toLocaleDateString(undefined)` left). |
| 6 | **Indic fonts + line-height** | **PASS (code)** | Noto Sans Devanagari/Bengali linked in `index.html`; `html[lang=hi/bn]` gets an Indic-first font stack and taller heading/body line-height; CSP allows exactly the two Google Fonts origins, pinned by `csp.test.ts`. *Rendering* must be eyeballed — see manual list. |
| 7 | **Server strings localised** (crisis, check-in, plan, insights) | **PASS** | `backend/app/content/i18n/{en,hi,bn}.json` load through the validated loader (required strings + placeholder whitelist); plan integration suite `test_plan_api_locale.py` (5 tests) and unit locale suite (16 tests) pass. |
| 8 | **Chat responds in the reader's language (Fake provider)** | **PASS** | New `backend/tests/chat/test_chat_api_locale.py` (6 tests): a Hindi/Bengali crisis message returns the pre-written card in that locale's own words (asserted by Unicode script range); a benign Indic turn reports the reader's locale; the system prompt handed to the model names Hindi. Real LLM unavailable offline — the instruction is the part this codebase controls. |
| 9 | **Crisis card localised + region helplines** | **PASS** | `test_crisis_api.py` parametrises every available locale: `locale`, `crisis.locale`, no fallback flag; BCP-47 (`hi-IN`) resolves; unknown locales fall back to English with the flag set. Region IN serves 112/Tele-MANAS/etc. per `helplines.json`. |
| 10 | **Clinical instruments stay English-only** | **PASS** | PHQ-9/GAD-7 items served verbatim; AssessmentsPage shows the English-only notice (`assessments.englishOnly`); no machine-translated items anywhere in the repo (locale trees contain chrome only). |
| 11 | **AI translations marked for review** | **PASS** | `translations/STATUS.md`: every hi/bn key group 🤖 "needs native-speaker review", per-group table, empty sign-off log; lists what deliberately stays English. No review is claimed. |
| 12 | **Eval dataset ≥ 60 cases per language** | **PASS** | en 241 · hi 92 · hi-Latn 118 · bn 121 (dev+test combined). |
| 13 | **Per-language crisis recall** | **PASS — 1.000 in every cell with crisis cases** | dev split: en 1.000 (22 crisis cases), hi-Dev 1.000 (8), hi-Latn 1.000 (10), bn 1.000 (11). Exact-level recall gaps remain (documented in the eval report; worst cells: en Severe hopelessness 0.000/6, hi-Dev Intent/plan 0.000/2, bn Intent/plan 0.200/5) — the ensemble never misses a crisis, it over-calls benign text instead. |
| 14 | **Tests fully offline** | **PASS** | Suite ran with the sandbox network allowlist (no HF hub, no fonts fetch); `HF_HUB_OFFLINE=1` guard fixture active. |

## What the sandbox cannot do (manual checklist for a real browser)

1. **Screenshots of 5 key screens in hi and bn** (home, chat, mood check-in,
   journal, settings) — no Chromium available here (Playwright browsers and
   Google Fonts are off the sandbox allowlist). Toggle the language switcher,
   screenshot each.
2. **Font rendering at 360px** in Hindi and Bengali: check matras are not
   clipped on headings (line-height bump) and Noto loads (Network tab →
   fonts.gstatic.com); with fonts blocked the OS fallback should still be
   legible.
3. **Pseudo mode by eye**: open `/?pseudo=1`, walk five screens — every string
   should be accented and bracketed; any plain English is a leak.
4. **Switch mid-session**: change language on Home → Insights/plan copy should
   refetch in the new locale (query key includes locale); a plan generated in
   Hindi today stays Hindi for the day (server rule).
5. **CSP in production**: `docker compose up` and confirm the font stylesheet
   loads under the nginx CSP (dev-server allowance is separate).

## Known issues (carried in PROGRESS.md)

- hi/bn are AI-produced drafts pending native-speaker review (STATUS.md).
- Helpline names/descriptions and backend exercise instructions / journal
  prompts remain English (content, not chrome — needs the review pipeline).
- The sandbox cannot render screenshots; items above need a real browser.
- The dev/test env disables the safety ML path by design
  (`SAFETY_ML_ENABLED=false`), so Indic crisis detection in tests relies on
  rules; the ML raise path is measured by the evals instead.
