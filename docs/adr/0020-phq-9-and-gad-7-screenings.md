# ADR 0020 — PHQ-9 and GAD-7 screenings: the official instrument, help before the score

Date: 2026-10-11 · Status: accepted

## Context

Day 20 adds the first surfaces where Manovia asks a fixed set of clinical
questions and puts a number on the answer. Three risks shaped the design, and
each is a place where a plausible implementation is actively harmful:

1. **A screening score read as a diagnosis.** PHQ-9 and GAD-7 are validated
   *screeners*: they say how the last two weeks have been and whether a fuller
   conversation is worth having. Presented as a result, a band becomes a label —
   either false reassurance from a low score or a verdict from a high one.
2. **Item 9 is the suicidality item.** It is asked inside a depression
   questionnaire, so a person can answer it and then be shown a total, a band
   and an encouraging sentence. If the help is below the score, or behind a
   scroll, the app has buried the one line that mattered. The published rule is
   also specific: any answer above "Not at all" is a positive screen **whatever
   the total says**, and a total of 1 (or 0) must not hide it.
3. **Wording drift.** A paraphrased item is not a validated instrument, and a
   score a person brings to a clinician has to be comparable with the score the
   clinician computes. That means verbatim items, the published 0–3 response
   set, the published cutpoints, and a record of where the wording came from —
   including what the licence allows, so the question of reproduction is
   answered rather than assumed.

## Decision

**Verbatim, attributed content.** `backend/app/content/assessments/phq9.json`
and `gad7.json` carry the nine and seven official English items, the four
response options, the severity bands and per-band plain-language copy, and a
`provenance` block: the developers' credit, the licence sentence, the source URL,
the date the wording was checked, and an availability note. Both instruments come
from the official English sheets at `phqscreeners.com`:

- PHQ-9: <https://www.phqscreeners.com/images/sites/g/files/g10060481/f/201412/PHQ-9_English.pdf>
- GAD-7: <https://www.phqscreeners.com/images/sites/g/files/g10060481/f/201412/GAD-7_English.pdf>
- Instruction manual (cutpoints and band table): <https://www.phqscreeners.com/images/sites/g/files/g10016261/f/201412/instructions.pdf>

Developed by Drs. Robert L. Spitzer, Janet B.W. Williams, Kurt Kroenke and
colleagues, with an educational grant from Pfizer Inc. **"No permission required
to reproduce, translate, display or distribute."** That sentence, the developer
line and the source URL travel with the definition (the API's `provenance`
block and the result page's "Where this questionnaire comes from" `<details>`,
and the printable summary) so a clinician can see the instrument is the real one.
Where the published sheet omits a serial comma, so does Manovia: GAD-7 item 1
reads "Feeling nervous, anxious or on edge".

**Scoring is pure and published.** `services/assessments/scoring.py` sums the
0–3 answers, rejects a missing answer with `IncompleteAssessmentError` (a `422`
over HTTP) rather than treating it as zero, and maps the total to a band with the
instrument's own cutpoints: PHQ-9 0–4 minimal, 5–9 mild, 10–14 moderate, 15–19
moderately severe, 20–27 severe; GAD-7 0–4 minimal, 5–9 mild, 10–14 moderate,
15–21 severe. The cutpoints are data in the content file, not constants in code,
and the client never computes a score: the API returns the total, the band and
the band copy, and the printout prints what the API returned. One implementation
of the clinical rule exists, and the same sentence a person reads is the sentence
their printout shows.

**The item-9 rule lives in the content file and fires before the score.**
`safety.item_id` (`phq9_9`) and `safety.rule` (`answer_greater_than_zero`) are
data; the service applies them, the response carries the crisis flow (level,
rationale code `assessment.phq9_item9`, the pre-written message, regional
helplines and the emergency number) in its own field, and the UI renders the
helplines **first in the DOM**, before any number, in the same render as the
result — there is no frame in which a positive item 9 is on screen without the
help. The total is still shown underneath: item 9 adds help, it does not delete
the result. GAD-7 has no such item and no crisis path.

**Two deliberate absences.** No `safety_events` row is written for item 9: every
other crisis surface writes metadata-only evidence because a *detector* fired,
whereas here a person answered a question about themselves and that answer is
already stored with its instrument and timestamp; a row claiming a rules or ML
detection would be inventing evidence. And repeat submissions are never blocked:
checking in more often than every fortnight returns the cadence notice as advice
about how to read a trend, not a refusal.

**Presentation rules.** The score block always carries "This is a screening
result, not a diagnosis" with the band sentence from the content file; a trend
chart is hand-rolled SVG (no chart library — `/insights` is lazy-loaded precisely
to keep `recharts` out of the main bundle) with the line `aria-hidden` and a real
table twin; a single result is written and drawn as a starting point and never as
a direction; and printing yields the `.print-summary` section alone — instrument,
date, total, band, the verbatim disclaimer, the item-9 line when it applies, the
recent results table, the developer credit and the licence — because that page is
the only artefact that leaves the app and reaches a clinician.

## Trade-offs and follow-up

- **Stored in the clear, on purpose.** `assessment_results.answers` (a small
  JSON map) and `total` are not encrypted: they contain no free text, and a
  person reading their own history needs them; the note-like free-text fields
  that *do* need encryption live on mood and journal rows, where the Day-17
  envelope covers them. The trade-off is that a database copy reveals which
  screener somebody took and their scores; ownership is enforced on every read
  (another user's result is a `404`, not a `403`) and results are never sent to
  an LLM.
- **Item 9 is the only crisis trigger.** A high total without a positive item 9
  gets the band copy and "when to talk to someone", not the crisis card. That
  follows the instrument's own rule; inventing a second, score-based trigger
  would fire on a large share of honest answers about a bad fortnight and teach
  people to ignore the card that matters. Revisit only with clinical review.
- **English-only content.** The items are published in many translations, but
  each has its own validation history and its own licensing trail; Manovia ships
  the English sheet it verified. The crisis message itself is already localized
  by the existing helpline/template system. Multilingual instruments are parked.
- **No clinician review in this loop.** The wording, cutpoints and licence were
  checked against the official publications on 2026-10-11 and recorded in
  `provenance`; nothing here has been reviewed by a clinician, and the band copy
  is written by this project, not by the instrument's authors.
- **Frequency guidance is not enforced.** A person can submit twice within a
  minute and will simply read the cadence notice twice. That is intended.

See [safety design](../safety-design.md), the [model card](../model-card.md) for
the licence/availability statements, and the Day 20 verification report for the
real command output.
