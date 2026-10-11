# 0016 — Insights: statistics that refuse to answer, and charts that can be read without seeing them

Date: 2026-10-11
Status: Accepted

## Context

Day 16 is the first surface that **summarises** somebody's data instead of
recording it. That is a different kind of risk from anything built so far. A mood
check-in is a fact the person entered; an insight is a claim the app makes *about
them*, and a wrong claim here is worse than no feature at all — it can tell
somebody their low mood is "because of" their sleep, or manufacture a trend out of
three bad days, and be believed, because it is presented as a finding.

Four ways this feature goes wrong quietly, and each shaped a decision below:

1. **A pattern invented from nothing.** Three check-ins drawn as a line, two
   points called a trend, a tag that appeared twice producing a confident sentence.
   The failure is not the arithmetic; it is the *confidence*.
2. **Association stated as cause.** "Poor sleep is making you low" is one word
   away from "on days you tagged poor sleep, your mood was lower on average" and
   reads as a diagnosis. Statistical honesty is a copy decision as much as a
   modelling one.
3. **A chart nobody can read.** A line graph is unusable to a screen reader, to
   somebody with low vision, and to somebody who has no idea what an axis is. A
   visualisation with a `title` attribute is not accessible.
4. **A note leaking into a statistic.** Day 15's note is the most sensitive text in
   the product, and "let's just pass the rows through" is the natural way it ends
   up in a summary or a CSV without anybody deciding to put it there.

What already existed: `mood_entries` with `created_at`, two scales and a factor
document (Day 3); the closed emotion and factor vocabularies (Days 6, 15); a
localised content pipeline already used for crisis messages and check-ins
(Days 8, 15); the frontend's token-based design system and jest-axe harness
(Day 5); and a `tz`-per-request convention the streak already established (Day 15).

## Decisions

### 1. Everything is a pure function, and the minimum-data rules are the contract

`app/services/mood/insights.py` takes `(instant, valence, energy, emotions,
factors)` tuples and returns plain dataclasses. No HTTP, no database, no clock of
its own (`now` is a parameter), and the two randomness sources — bootstrap and
permutation — take an injected `random.Random`, so a report can be re-run and
compared.

The thresholds are the feature, not a detail:

* **Seven days with check-ins** before any trend is claimed. Six returns
  `not_enough_data` with a sentence saying what is missing. The type is
  `Literal["up", "down", "flat", "not_enough_data"]` rather than `str | None`, so
  "we do not know yet" is a value the UI must render, not a null it can skip.
* **Five days per group** before any comparison. A tag used twice returns no
  effect size, no interval and no p-value at all — not a wide one — plus a note
  naming the counts.
* The durations are *published in the response* (`min_entries_for_trend`,
  `min_entries_per_group`) so the UI states the rule instead of paraphrasing it.

*Alternative weighed:* a confidence tier ("low confidence — keep checking in")
instead of a hard floor. Rejected because a tier is still a claim: it invites
reading a number and adjusting for it, which is exactly the arithmetic nobody
asked the app to do on their behalf.

### 2. Effect size, interval and p-value — and no p-value without the other two

Every comparison reports Cohen's d, a 2000-resample bootstrap 95 % CI for the
difference, and a 2000-resample two-sided permutation p-value, all three in the
API response and in the CSV. The p-value is never shown alone: "p = 0.03" without
an effect size lets a trivial difference look important, and without a sample size
lets a comparison of four days look like a study.

*Alternative weighed:* a t-test (parametric, and mood scales are bounded 1–5 with
no reason to be normal) and a Mann-Whitney U (better, but it reports ranks, not
the "about 0.8 points" this copy is built on). The permutation test answers the
question the sentence actually asks — could a difference this large have come from
the labels we happened to get? — and the bootstrap interval is the one a
non-statistician can read.

### 3. The type is the privacy guarantee: a statistic cannot be handed a note

There are **two shapes** in the module, and the difference is the safety property:

* `Entry = (datetime, int, int, list[str], dict[str, object])` — what every
  statistic takes. It has no note field.
* `ExportRow = Entry + (str | None,)` — what the CSV export takes, because the
  export is the person's own copy of their own data.

A function whose parameter type cannot carry a note cannot read one, however a
future caller feels about it. `test_a_note_is_never_part_of_a_pattern` plants a
canary note and asserts it reaches neither `/patterns` nor `/weekly`, while being
present exactly once — in the entries bundle of the export it belongs in. The
explainer states the same guarantee to the person: "Your check-in notes are never
used for these calculations."

*Alternative weighed:* pass one shape and document that the note is ignored. A
comment does not survive a refactor; a type does.

### 4. Associative wording, enforced by a test rather than by review

The generated sentence is:

> On days you tagged sleep, your mood was 1.1 points lower on average
> (n=19 vs 41). This is an association, not a causal link.

`tests/unit/test_mood_insights.py`, `tests/integration/test_insights_api.py`,
`frontend/src/features/insights/copy.test.ts` and `scripts/verify_day16.py` each
scan generated copy for a different blocklist of causal verbs, cheerleading and
diagnostic vocabulary. `verify_day16.py` scans **73 real strings** end to end,
built from the frontend's own copy module by esbuild rather than retyped. It also
checks the negative: no Devanagari has leaked into the Bengali template, which is
what caught three corrupted words in the first draft of it.

### 5. Every chart ships three things: a sentence, a table, and a summary region

Each chart renders

1. a **text summary** that states the finding with the numbers in it ("7-day
   rolling average over 7 days with data. Average valence 3.2 … The trend is
   upward."), in a `role="region"` named uniquely per chart so the five landmarks
   on the page are distinguishable;
2. a **data table alternative** in a native `<details>`, so it is keyboard
   operable with no ARIA and every value is present even when a bucket's average
   is withheld; and
3. the chart itself inside `role="img"` with a described label.

The palette is Okabe-Ito (blue/vermillion/bluish-green/…), which stays
distinguishable under protanopia, deuteranopia and tritanopia, and valence and
energy differ by **dash pattern as well as hue** — colour is never the only
channel. A test asserts the hex values are the named ones, so a theme refactor
cannot quietly swap a chart colour for a token that is contrast-poor in one of the
two palettes.

*Alternative weighed:* a chart library's own accessibility features. They provide
a label, not an alternative — "bar chart" is not an answer to "is my mood
getting worse?".

### 6. Recharts is lazy; the initial bundle does not pay for it

Three charts on one page meant +454 kB of JavaScript in the bundle that loads the
chat surface — for a product used on mid-range phones in India. The three charts
are `React.lazy`, so Recharts lands in its own chunk (347 kB, 103 kB gzipped) that
only the insights route downloads. The build confirms it: the main chunk contains
no `recharts` module, and the route chunk exists. A `Suspense` fallback says
"Loading the charts…" rather than showing an empty box.

### 7. The weekly summary is a template, in three languages, with its limitation attached

No model is involved — `weekly_summary` composes sentences from a dict of `en`,
`hi` and `bn` templates. That is why the route needs `terms` + `privacy` and
deliberately **not** `ai_disclosure`: no model is in this path, and asking
somebody to agree about AI before reading their own averages would be a lie about
what happens to their data. Every language's summary ends with its own
"pattern summary, not a diagnosis" sentence, verified word by word — a summary
that reads like a verdict is worse than no summary, in any language.

The window is the seven **complete** days ending yesterday, so "this week" never
changes shape depending on the hour it is opened. The clock is a seam
(`app.api.v1.insights._now`) for the same reason the streak has one.

### 8. CSV export, written by `csv`, with the note column honest

The export returns three bundles (entries, associations, time patterns) because
they have three shapes, and a single merged sheet would be worse than three
readable files. Written with Python's `csv` module, not string formatting: a note
contains commas, quotes and newlines, and `test_a_note_with_commas_and_quotes_does_not_shift_the_columns`
parses the output back so a hand-rolled writer that escaped only the obvious case
would still fail. `undefined` statistics are empty cells, never the string `"None"`.

### 9. The false-positive rate is a number this repository reports, not a hope

Twenty independent noise seeds — 40 comparisons, no planted effect anywhere — go
through the shipped statistics, and **0 of 40 cleared both p < 0.05 and |d| > 0.8**.
`scripts/verify_day16.py` prints the rate, the per-seed breakdown, and how many
comparisons reached p < 0.05 alone (1 of 40, 2.5 % — which is what a p < 0.05
threshold is *supposed* to produce on noise). The same guarantee is asserted
through the HTTP path on three of the seeds and in-suite on all twenty, so a
regression is caught by CI rather than by a hand-run report.

*Alternative weighed:* report only the tests, since the feature already withholds
claims below its minimums. Rejected: the minimums are about *how much* data, not
about whether the data means anything, and only a measured false-positive rate
shows the difference.

## Consequences

* An insight is never a number without its provenance: every claim carries sample
  sizes, and every withheld claim says what it would need.
* The `Entry` type makes "no note in the statistics" structural. Adding a note to
  a statistic is now a visible type change in review, not a silent one.
* Charts cost nothing on the chat route and 103 kB gzipped on the insights route.
  `React.lazy` inside a `Suspense` also means the text summaries and tables are the
  *first* thing rendered for those buckets.
* Every generated sentence is scanned by three independent blocklists across the
  backend, the frontend and the verification script. That is more places to keep
  in step, and it is deliberate: one list would be one thing to weaken.
* The thresholds are hard-coded constants with tests that pin their values, so
  changing them is a product decision that fails CI until it is made on purpose.
* The weekly summary says "Average mood: 3.0/5 … over 3 check-in(s)" while also
  saying the trend cannot be claimed from three check-ins. That is intentional —
  an average of what was recorded is a fact, a trend is a claim — but it is the
  one sentence a reader might find contradictory, and it is listed as a known
  issue to re-read with fresh eyes.
* Hindi and Bengali weekly-summary templates have had no native-speaker review.
  The English one is the reviewed artefact; the other two are structurally
  checked (script, disclaimer, no mixed-script leakage) and honest about being
  unfinished.
