# ADR 0023 — Multilingual: English, Hindi, Bengali — in that order of trust

Date: 2026-10-12 · Status: accepted

## Context

Manovia's readers in India carry feelings in Hindi and Bengali, and until Day 23
the whole interface answered them in English. Three forces shaped this day:

1. **A translation nobody checked is a promise made by a machine.** The safety
   copy, the crisis card and the onboarding agreements are the most
   consequential words in the product. Shipping them machine-translated and
   *calling them done* would be the worst possible version of multilingual.
2. **Clinical instruments are not prose.** The PHQ-9 and GAD-7 are validated in
   specific published wordings. Translating them in-house would produce
   questionnaires that measure nothing the cutpoints were built for — a
   screening worse than no screening.
3. **Language is a preference *and* a request.** The interface language is a
   saved choice; the language of a chat reply is per-turn (the reader may write
   in Hindi while the interface stays English). The two must be decidable
   separately and must not fight.

## Decision

### 1. One locale tree per language, bundled, structurally pinned

The frontend ships `en.json`, `hi.json`, `bn.json` next to the code, loaded
synchronously by i18next at startup — no runtime fetch, no blank flash, no
network dependency for the first painted word. All three trees carry exactly
the same keys with exactly the same `{{placeholders}}`; a vitest guard
(`locales/parity.test.ts`) fails the build on any drift. Copy modules read
`t()` at render time (getters / zero-arg functions), never at import time, so
a language switch re-reads every string without a reload.

### 2. Resolution order: saved choice → browser → English

`localStorage` (`manovia.language.v1`) wins; otherwise the browser's primary
language subtag (`hi-IN` → `hi`); otherwise `en`. The same choice drives
`Intl` formatting (`lib/dates.ts` → `intlLocale()`), the `<html lang>`
attribute (screen-reader voice + Indic CSS), the `locale` parameter on plan /
insights / assessments API calls, and the chat's default language.

### 3. The server speaks the reader's language — with one stickiness rule

Server-authored text (crisis messages, check-in lines, plan titles/reasons,
insight templates) lives in `backend/app/content/i18n/{en,hi,bn}.json`,
validated at load against a required-string list and a placeholder whitelist.
The plan is generated once per local day, and **the first request's language
sticks for that day**: a plan that started in Hindi does not swap tongues
mid-day if the interface language changes. Chat takes `locale` per turn and
instructs the model in that language; detection is a tie-breaker only, never
an override of an explicit choice.

### 4. Detection expanded for Indic scripts and romanised variants

The safety rule set gained Devanagari and Bengali script patterns plus their
common romanisations and euphemisms (`patterns_indic.yaml`). The eval dataset
grew past the ≥60-cases-per-language floor for every language variant
(en 241, hi 92, hi-Latn 118, bn 121). The recall-first posture is unchanged:
crisis recall 1.000 in every language/culture cell that has crisis cases, at
the documented precision cost on benign text.

### 5. What stays English, and why

- **PHQ-9 / GAD-7 items** — shown exactly as published, with an in-app notice,
  until officially validated Hindi/Bengali editions are sourced with
  provenance (licence + validation citation). Never machine-translated.
- **The `DELETE` confirmation word** — must byte-match the backend's expected
  literal in every language.
- **Helpline names/descriptions** (`helplines.json`) and backend-served
  exercise instructions / journal prompts — content, not chrome; localising
  them needs verification or the review pipeline below, not a translation
  pass. The UI says so where it matters (the exercises `framing` line).

### 6. AI-produced translations carry a visible debt

Everything in `hi`/`bn` was produced by an AI and is marked, per key group,
🤖 *needs native-speaker review* in `translations/STATUS.md`. The file has a
sign-off table that is deliberately empty; a cell flips to ✅ only with a
named reviewer and a date. This is the honest middle ground between "English
only" and "translated, trust us".

### 7. Pseudo-localisation is the hard-coded-string detector

Pseudo mode (`?pseudo=1`, or the switcher entry) renders every string
accented, ~35% longer, wrapped in ⟦ ⟧ — so any surviving plain-English string
was not routed through the layer, and any container that overflows for
Hindi-length text overflows *now*. A post-processor in i18next does the
transform; it is a no-op for real locales. (The day's own tests caught that
registration without the `postProcess` init option activated nothing — fixed,
and now pinned by tests.)

### 8. Indic rendering: real fonts, honest CSP

`index.html` links Noto Sans Devanagari / Bengali (display=swap); under
`html[lang=hi]` / `[lang=bn]` those faces lead the font stack and heading /
body line-height grows so matras never clip. The CSP allows exactly two new
origins — `fonts.googleapis.com` (stylesheet) in `style-src`,
`fonts.gstatic.com` (font files) in `font-src` — and `csp.test.ts` pins that
scoped allowance. Offline/dev without those origins degrades to OS fonts; it
never blocks.

## Consequences

- Language switching is instant and reversible; nothing re-downloads.
- Any new UI string must exist in three files or the parity test fails — the
  drift is caught at build time, not in front of a reader.
- The hi/bn trees are drafts by their own ledger. Shipping them to real users
  should wait on the sign-off table in `translations/STATUS.md`; the app is
  honest about that state in-repo.
- One third-party allowance entered the CSP, narrow and pinned; the day-22
  security posture is otherwise unchanged.
