# Translation status

Day 23 (Multilingual: English, Hindi, Bengali). This file is the honest ledger
of what has been reviewed by humans and what has not. **Nothing in the `hi` or
`bn` columns below has been reviewed by a native speaker yet.** Every entry
there is AI-produced and must be treated as a draft until a native-speaker
review signs it off per key group.

Legend:

- ✅ **Reviewed** — checked line-by-line by a native speaker; safe to rely on.
- 🤖 **AI-produced, needs native-speaker review** — usable as a shipping draft;
  do not claim otherwise anywhere.
- ⛔ **Deliberately English-only** — kept in English on purpose (see notes).

## Frontend interface (`frontend/src/lib/i18n/locales/*.json`)

| Key group | en | hi | bn | Notes |
| --- | --- | --- | --- | --- |
| `app`, `nav`, `language` | ✅ source | 🤖 | 🤖 | UI chrome. |
| `crisis` (card + modal) | ✅ source | 🤖 | 🤖 | Helpline *names/descriptions* come from the backend `helplines.json` and stay English (see Backend content). |
| `home`, `chat` | ✅ source | 🤖 | 🤖 | Chat error cards, starters, mood-hint words. |
| `mood` (form, scales, tags, streaks, actions) | ✅ source | 🤖 | 🤖 | Stored tag/emotion *values* stay the English literals the backend validates; only display labels translate. |
| `exercises` | ✅ source | 🤖 | 🤖 | Exercise *instructions* arrive from the backend and remain English (see Backend content); the `framing` string says so in-app. |
| `assessments` | ✅ source | 🤖 | 🤖 | **Instrument items stay English-only** (⛔ for the questionnaire text itself) — see Clinical instruments below. UI chrome around them translates. |
| `insights` | ✅ source | 🤖 | 🤖 | Insight sentences arrive localised from the backend when templates exist; `localeFallback` covers the rest. |
| `journal` | ✅ source | 🤖 | 🤖 | Fallback templates translate; backend-served prompts are English for now. |
| `cbt` | ✅ source | 🤖 | 🤖 | Step labels, traps panel, record list. |
| `plan`, `settings`, `privacy`, `onboarding`, `notFound` | ✅ source | 🤖 | 🤖 | Includes onboarding agreements and the AI disclosure wording. |
| `pwa` (Day 25: offline banner, update notice, install offer, offline sync, page-load errors) | ✅ source | 🤖 | 🤖 | Offline copy must stay literally true: "kept on this device", and "sent once you're back online **with Manovia open**" (there is no background sync). `src/pwa/offlineCopy.test.ts` checks that hi/bn keep the "Manovia open" condition. Review against the behaviour in ADR 0025, not just the English. |
| `a11y` (Day 25: accessibility statement) | ✅ source | 🤖 | 🤖 | `limit1` says the hi/bn text awaits native review — update it in all three languages when this ledger changes. |
| Day 25 additions inside `mood`, `journal`, `crisis.modal`, `home`, `exercises`, `settings` (offline queue, local drafts, offline helpline notes, contrast switch) | ✅ source | 🤖 | 🤖 | Same review discipline as their groups; `exercises.offline` was *changed* (offline reloads now work) in all three languages. |

Structural parity (same keys, same `{{placeholders}}`) between en/hi/bn is
enforced by `frontend/src/lib/i18n/locales/parity.test.ts`.

## Backend content (`backend/app/content/i18n/*.json`)

| Group | en | hi | bn | Notes |
| --- | --- | --- | --- | --- |
| Crisis messages & resources copy | ✅ source | 🤖 | 🤖 | Loaded by `backend/app/content/i18n.py`; placeholder whitelist enforced by the loader. |
| Check-in lines / suggested actions | ✅ source | 🤖 | 🤖 | Same loader. |
| Plan text (titles, reasons) | ✅ source | 🤖 | 🤖 | First request's language sticks per local day. |
| Insight templates | ✅ source | 🤖 | 🤖 | Rendered server-side in the requested locale. |

### Deliberately English-only (⛔ — do not translate until sourced properly)

1. **PHQ-9 and GAD-7 items** (`backend/app/content/assessments/*`): shown
   exactly as published, in English, with an in-app notice. Machine-translating
   a clinical instrument invalidates it; officially validated Hindi and Bengali
   editions must be sourced with provenance (licensing + validation citation)
   before any translation ships. Nothing here is ever machine-translated.
2. **`DELETE` confirmation word** (privacy section): must match the backend's
   expected literal exactly, in every language.
3. **`helplines.json` names and descriptions**: backend content; localising it
   needs verification of each entry, not translation of prose. Tracked as a
   known issue in PROGRESS.md.
4. **Exercise instruction bodies and journal writing prompts** served by the
   backend: content, not chrome; localising them is a follow-up with the same
   review discipline.

## Review sign-off log

| Date | Locale | Key group | Reviewer | Outcome |
| --- | --- | --- | --- | --- |
| — | — | — | — | No native-speaker review has happened yet. |

When a review happens: record it here, flip the matching table cells from 🤖
to ✅, and note any corrections made. Never mark ✅ without a named reviewer
and a date.
