# ADR 0018: Declarative guided exercises and a local, accessible runner

- **Status:** Accepted (Day 18)
- **Date:** 2026-10-11

## Context

Exercises must be reviewable without reading animation code, usable without
sound or a live provider, and must not turn a before/after rating into a claim
of treatment efficacy. Optional breathing holds and inward attention need clear
ways to opt out. The existing app already has consented guest accounts, numeric
mood metadata, shared motion preferences, and an Exercises navigation placeholder.

## Decisions

1. **Content is data, never generated at runtime.** Eight definitions live in
   `backend/app/content/exercises/*.yaml` and ship in the backend wheel.
   `services/exercises/engine.py` uses a duplicate-key-rejecting SafeLoader and
   strict Pydantic models (`extra=forbid`). Durations are positive integer seconds,
   sum exactly to the advertised duration, and step/choice IDs are unique.
   A bad file rejects the entire catalogue before the app serves requests.
   Every step has visible, audio-free text; every exercise has contraindications,
   notes, tags, and a plain-language evidence limitation. English instructions
   are explicit; Hindi/Bengali **titles only** are included, not a claim of a
   translated or clinically reviewed programme.
2. **Public content, private history.** `GET /api/v1/exercises`, `/{id}` and
   `/recommendations` need no account. `POST /{id}/complete` and `GET /history`
   require a live user and current terms/privacy consent. No LLM, audio provider,
   raw message, or planner answer enters the completion path. Optional ratings
   are strict integers 1–5 (higher means feeling better), not inferred severity.
   Like mood scales, they are numeric metadata in the clear, not encrypted prose.
3. **Idempotent self-reported completions.** A client UUID is the completion PK.
   Identical retries return the original row; changed payloads or another owner
   return a generic conflict. The database PK resolves concurrent retry races.
   Alembic `0005` adds the portable, constrained table, a user/time index, and
   account-deletion cascade. Store the public title, definition hash and duration
   at completion so later content edits do not rewrite history. This is not a
   server-verified adherence or effectiveness score.
4. **Association, not causation.** History says what the person _reported_
   before/after, including lower or unchanged ratings without failure wording.
   Each comparison explicitly says it is not proof the exercise caused a change.
   Missing ratings never become zero; a comparison requires both.
5. **Small auditable recommendation rules.** Use the existing nine-label emotion
   vocabulary and an optional, person-selected intensity. High anxiety/fear
   offers outside-focus grounding first and optional box breathing second.
   Every result carries a “why this?” reason and no promise of benefit. A manual
   choice never claims to be a diagnosis. The selection is not persisted.
6. **The runner owns timing locally.** CSS/SVG is decoration; the text and countdown
   are authoritative. A 250ms interval consumes guarded `Date.now()` deltas (not a
   monotonic clock). Manual pause includes the fraction since the last tick. A hidden
   tab, negative clock delta, or delayed tick over two seconds pauses instead of
   silently skipping instructions/holds; returning never auto-resumes. CSS phase
   classes are literal so Tailwind retains them; a component-only generated-CSS test
   and browser computed-animation check protect that boundary. Announce steps, not
   every timer tick. Reduced motion (or an explicit text-only choice) removes the
   animated SVG and uses a static countdown. Haptic cues are off by default,
   feature-detected, optional, and independent of the text.
7. **Offline means the already-loaded tab.** Fetch the entire catalogue once, then
   run without network access. A failed completion save stays only in React memory,
   clearly marked unsaved, with an explicit same-UUID retry. No personal rating or
   planner choice is persisted to web storage, and no background syncing occurs.
   Reloading/closing an offline tab loses unsaved state; an offline-reload/PWA cache
   and durable encrypted queue are intentionally outside Day 18.

## Consequences and limits

- No secret, external model, microphone, or sound permission is needed.
- Content and translations still require qualified human review; evidence notes
  are limitations, not citations of efficacy or medical advice.
- The timer is an aid, not a requirement to force breaths or stay with distress.
  People can breathe normally, shorten counts, skip inward attention, or stop.
- A client can self-report completion without running the UI; this is intentional.
- Vibration support/permission and actual screen-reader speech require device checks.
- Wider offline app-shell/crisis-resource caching must be designed together later,
  rather than silently caching authenticated requests or promising offline help.
