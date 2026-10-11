# 0015 — Mood check-ins: a ten-second form, a screened note, and a streak that cannot be broken by a bad week

Date: 2026-10-11
Status: Accepted

## Context

Day 15 adds the first surface where somebody records how they feel on a scale and
the app stores it. Three risks shaped the design, and each one is a place where a
plausible implementation is actively harmful:

1. **A streak is a guilt machine.** The familiar version — miss a day, lose the
   number — punishes exactly the days a person cannot face a check-in. On a
   mental-health product, the mechanic that is supposed to bring somebody back is
   the one most likely to drive them away.
2. **The optional note is free text**, and free text is where somebody writes the
   sentence they would never say out loud. It must be screened before it is
   stored, and it must be unreadable at rest — but the check-in itself must never
   be *refused* for being frightening, because that punishes honesty.
3. **A check-in is tiny.** Valence, energy, a few tags, a note. Anything that can
   take longer than ten seconds, or that asks for a diagnosis, is out of scope by
   construction.

The data layer already had `mood_entries` (Day 3), the safety engine already
produced levels, pre-written crisis messages and regional helplines (Days 8–9),
and the frontend already had the token-based design system, the shared `CrisisCard`
and the chat's crisis-gating pattern (Days 5, 12).

## Decisions

### 1. Two bounded scales and closed vocabularies; unknown tags are an error

`POST /api/v1/mood` takes `valence` and `energy` (1–5, `CHECK`-enforced in the
database), at most three emotions from the nine-label taxonomy the analyser already
speaks (`app.services.nlp.base.EMOTIONS`, re-exported — not copied — by
`app/services/mood/taxonomy.py`), at most five of eight context factors
(`sleep`, `study`, `work`, `family`, `friends`, `health`, `money`, `weather`), and
an optional note of at most 1000 characters. There is no free text other than the
note, and no score is computed or shown.

An unknown tag is a `422` naming the field, never a silent drop: quietly discarding
a tag would store a *different* mood than the one reported. Tags are lower-cased and
de-duplicated so a double tap cannot fail a check-in, and the order the person chose
is preserved.

### 2. Screen, then store — and the check-in survives its own crisis

When a note is present the rules engine and the Day 9 ensemble run on it *before*
the row is written (`run_ensemble` → `Escalator.plan`). If the level is HIGH or
IMMINENT the response carries `crisis`: the level, the stored four-tier value, the
matched categories, stable rationale codes, the pre-written localised message, the
emergency number and the region's helplines — the same shape as `POST /crisis/assess`,
so the UI learns one crisis contract, not two. The check-in is saved anyway, and a
metadata-only `safety_events` row records the tier and which detector earned it.

Refusing to store an honest check-in because of what it said would teach the person
to stop telling the truth. The opposite failure is worse, though: if the safety
engine cannot run, the request fails **closed** with `503 safety_unavailable` and
nothing is written. Unscreened free text is the one outcome that is never allowed.

### 3. Mood consent is `terms` + `privacy`, not `ai_disclosure`

No model is in this path: the safety check is a local deterministic engine and the
suggested action is a fixed frontend mapping. Asking somebody to agree about AI
before they may say how they feel would misdescribe what happens to their answer.

### 4. The streak is counted in the caller's timezone, with forgiving rules

The zone arrives per request (`tz`, an IANA name) because the schema has no user
timezone column yet, and an unknown name is a `422`, not a silent UTC fallback.
Omitted or blank means UTC, and the response says `timezone_fallback_used: true` so
the client can see that its streak does not match the calendar in the person's hand.

The rules (`app/services/mood/streak.py`, pure functions over local dates):

- **Today is still open.** Not having checked in yet today never ends a run.
- **One missed day in any rolling seven is bridged** (a "streak freeze"). Each
  empty day has to buy its own freeze, so four consecutive empty days cannot be
  spanned by one allowance.
- **Two missed days in a row reset the count, never the welcome.** Coming back after
  a gap yields the `returning` state, with the previous best still reported and
  copy that says starting again counts the same.
- The state enum is `never` / `today` / `open` / `returning`. There is deliberately
  no `broken`, `lost` or `at risk`: a state machine that can only describe the past
  gently cannot be rendered cruelly by accident.

`current_streak` counts days with a check-in; bridged days are reported separately
as `freezes_used`, which is what makes "you can miss one in any week"
(`freeze_available`) a true sentence.

### 5. One clock, one conversion, and no notes in the streak query

Local days are derived in one place (`local_date`), and a naive timestamp is read as
UTC. The API has a single clock seam (`_now`), and the row is stamped with the same
reading that decided what "today" is: a check-in that lands either side of local
midnight therefore always appears in the streak returned with it. The streak query
projects `created_at` only (`MoodRepository.list_instants`), so notes never leave the
database for a calculation that cannot use them.

### 6. Ranges: a local day is a local day, and `to` includes the named day

`GET /api/v1/mood?from&to&tz` accepts ISO dates or ISO timestamps. A date-only bound
is resolved to the local midnight in `tz` (so a DST day is 23 or 25 hours long and
`start + 24h` is never used), `to` is inclusive of the named day, and a timestamp is
an exact instant. A backwards range is a `422 invalid_range`. Results are newest
first — one order, documented, rather than an `order=` parameter nobody needs yet.

### 7. Ownership is structural

Every route reads only the caller's rows; there is no id-addressed read, so an id
cannot be probed at all. Deleting somebody else's check-in is a `404
mood_entry_not_found`, identical to one that never existed — a `403` would confirm
it exists. `DELETE` is a hard delete (the note goes with the row) and returns the
remaining streak, because deleting a check-in is a legitimate act, not a mistake to
be punished.

### 8. The words are a reviewed artefact, and they live in the UI

The server returns numbers, states and dates; the sentences live in
`frontend/src/features/mood/copy.ts` so they can be reviewed **as copy**. A unit test
scans them for guilt and cheerleading vocabulary, and `scripts/verify_day15.py` scans
them again with an independent blocklist and prints every string for a human read.
The confirmation offers one small optional action chosen by a deterministic mapping
(`suggestedAction.ts`) — never a score and never a promise.

The daily reminder is a device-local preference (`localStorage`, key
`manovia.mood.reminder.v1`, `{enabled, time}`) and the copy says plainly that
reminders are not being sent yet. No push infrastructure is in this milestone.

## Consequences

- A person can check in with the keyboard alone (radio group, range input, checkbox
  chips, a textarea, one button), and every scale point carries a word as well as a
  face, so nothing is colour-only. The e2e spec drives that path in a real browser
  on a machine that has one.
- A worrying note produces help *and* a stored check-in; the audit row carries only
  the tier and the detector. The regression risk is the reverse: a note that reads
  as HIGH but is really a quotation or a worry about somebody else. That is the
  escalation policy's existing problem, and mood inherits its tests rather than
  inventing a second interpretation.
- Because the timezone is per request, a client that sends the wrong zone gets a
  streak for the wrong calendar — visible in `timezone` / `timezone_fallback_used`,
  and a user-level timezone column plus a trusted device default is the natural
  follow-up.
- One missed day per week is a *product* rule, not a setting. Making it
  configurable would turn a kindness into a policy somebody can be measured against.
- The vocabulary lives twice (Python and TypeScript), which is deliberate — the
  frontend must render chips without fetching a taxonomy — and a drift check in
  `scripts/verify_day15.py` fails if the two ever disagree.
