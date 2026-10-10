# Safety design — crisis detection and response

First version, Day 8 (2026-10-10). This is the design document for the module that
decides whether somebody needs a helpline number instead of a chatbot.

It is written for two readers: a maintainer who has to change detection without
breaking it, and a reviewer who has to decide whether to trust it. Where a
decision has a cost, the cost is stated next to the decision.

Companion documents: [ADR 0008](adr/0008-crisis-detection-rules-engine.md) records
the decisions and their alternatives; this file describes the system as built.

---

## 1. What this module is, and what it is not

Manovia is a privacy-first mental wellbeing self-help companion. It is not therapy,
not a diagnostic tool, and not a crisis service. It cannot send help to anybody.

What this module does is narrow and specific: **read one message, decide how
worried to be, and make sure a person in crisis is handed a real human being's
phone number instead of a generated paragraph.**

AGENTS.md rule 1 makes it a gate, not a feature: crisis detection runs *before*
any LLM call, and a high-risk message gets a deterministic pre-written response.
Nothing downstream may overrule that — not a model, not a user preference, not a
prompt.

### Why rules and not a classifier

The Day 6 emotion model and an LLM are both available, and neither is used here.

A classifier outputs a probability. A probability is not something to put between
a person in crisis and a helpline, because there is no threshold that is right:
too low and every bad day gets a crisis card, too high and the misses are
fatal. Worse, it cannot be audited — when it fails, nobody can say why, which
means nobody can fix it except by retraining.

A rules engine outputs a level and a list of pattern ids. When it is wrong, a
reviewer reads `["si.want_to_die", "ctx.negated"]` and knows in five seconds that
a negation was detected and then not applied. That is fixable with a data edit.

The cost is honest and stated in §9: recall is bounded by what somebody thought to
write down. There is no statistical backstop for a phrasing nobody anticipated.

---

## 2. Data flow

```
message text
    │
    ▼
normalise()            app/services/safety/normalise.py
    │  primary, leet, spelling-corrected, collapsed variants
    │  squashed projection, clause index, truncation flag
    ▼
RuleEngine.assess()    app/services/safety/rules.py
    │  match 181 rules × every projection
    │  judge pragmatics per occurrence: negation, figurative
    │  drop suppressed hits; detect third person, quotation,
    │  fiction frame, timeframe, first person
    ▼
RiskAssessment         level + categories + rationale codes + context flags
    │                  (no text — see §7)
    ▼
Escalator.plan()       app/services/safety/escalation.py
    │  level → policy → template + region helplines + emergency number
    ▼
EscalationPlan         message, resources, actions, allow_llm, record_event
    │
    ▼
API / chat service     GET /api/v1/crisis/resources?region=IN
                       POST /api/v1/crisis/assess
```

Three layers, each with one job. `normalise` knows about text and nothing about
risk. `rules` knows about risk and nothing about responses. `escalation` knows
about responses and nothing about text — it takes an assessment, not a message.

That separation is what makes the privacy guarantee structural rather than a
convention: the layer that builds the response never receives the message.

---

## 3. The pattern vocabulary

181 rules across four YAML files in `app/content/safety/`:

| file | rules | what it holds |
| --- | --- | --- |
| `patterns_core.yaml` | direct statements | the eight risk categories in plain English |
| `patterns_euphemisms.yaml` | indirect language | "check out for good", "already gone", spacing and letter tricks |
| `patterns_indic.yaml` | Hindi and Bengali | romanised and native script |
| `patterns_context.yaml` | 0 rules | the pragmatics vocabulary (§5) |

By level: 115 high, 41 medium, 12 low, 13 imminent. By language: 136 English, 24
Hindi, 21 Bengali. By kind: 158 regex, 23 phrase.

By category:

| category | rules | notes |
| --- | --- | --- |
| `suicidal_ideation` | 73 | the largest set, and the one most exposed to figurative speech |
| `severe_hopelessness` | 31 | mostly medium; high when it implies absence ("no reason to live") |
| `intent_plan` | 22 | what separates a wish from an arrangement |
| `self_harm` | 17 | |
| `access_to_means` | 12 | **presence only, never use** — see §8 |
| `acute_medical` | 10 | something has already happened; an ambulance, not a conversation |
| `harm_to_others` | 8 | |
| `abuse_disclosure` | 8 | |

Every rule declares:

```yaml
- id: si.want_to_die          # stable identifier; becomes a rationale code
  category: suicidal_ideation
  level: high
  kind: regex                 # or phrase
  value: "\\bwant(?:s|ed)?\\s+to\\s+die\\b"   # matches NORMALISED text
  negation_sensitive: true    # may a preceding "not" cancel this?
  figurative_prone: true      # may benign context suppress this?
  figurative_subject: false   # is an inanimate subject positive evidence?
  language: null              # hi/bn enable post-verbal and in-span negation
  note: "why this level"      # prose for the next reviewer
```

The loader rejects unknown keys, uppercase values (matching happens on normalised
text, so an uppercase pattern can never fire), and any file that fails to compile.
A typo in the data is a startup failure, not a silent gap in detection.

Values are written against normalised text: lowercase, apostrophes removed
(`don't` → `dont`), punctuation converted to spaces. Writing a pattern with an
apostrophe in it is a bug that will never fire, and the loader says so.

---

## 4. Normalisation: several views of one message

People obfuscate, misspell, stretch letters and switch scripts. Each of those is a
different transformation, so `normalise()` produces several views and the engine
searches all of them.

**Variants** — full citizens, with clause boundaries and pragmatics intact:

1. `primary`: invisible characters stripped, NFKC applied (so Indic matras compose
   consistently), apostrophes *deleted* rather than replaced with a space
   (`don't` → `dont`, not `don t`), casefolded, punctuation converted to spaces.
   The punctuation step keeps Indic combining marks — a naive `[^\w\s]` class
   destroys Devanagari and Bengali vowel signs and silently breaks every native
   script rule.
2. `leet`: the primary with `0→o 1→i 3→e 4→a 5→s 7→t 8→b @→a $→s !→i |→l`.
3. `corrected`: 40 curated misspelling fixes applied longest-first, so
   `sucide`→`suicide` cannot be partly rewritten by a shorter rule. Covers both
   English (`hopless`, `dieing`, `kil`) and romanised Indic (`aatmahatya`,
   `zindgi`, `atmohotta`).
4. `collapsed_variant`: repeated letters collapsed *and then* spelling-corrected,
   because a message can do both at once. `kiiiillll meee` → `kil me` → `kill me`.
   This is a variant rather than a projection precisely because collapsing never
   joins two words, so clause boundaries and negation still work in it.

**Projections** — text with the spaces or repetitions destroyed:

- `squashed`: all whitespace removed. `iwanttodie`. Matched by dedicated fused
  patterns (`eu.fused_wanttodie`), because an ordinary pattern expecting `\s+`
  cannot match welded text.
- `collapsed`: repeated letters collapsed. `diiiie` → `die`.

Input is capped at 4000 characters; a longer message is cut and the assessment
says so via `input.truncated` and `context.truncated`. Cutting is a compromise:
risk usually appears early, and an uncapped message is a denial-of-service vector
against a module that runs synchronously in the request path.

### 4.1 The rule that makes this safe

Projections lose the information pragmatics needs. In `idonotwanttobedead` the
word "not" is no longer a token, so the negation scan cannot find it.

**A derived view can add a hit the honest text hid. It can never cancel one.**

Concretely: the primary text is judged first and alone, and a rule it judged
negated or figurative is skipped in every derived view of it — the leet,
spelling-corrected and collapsed variants, and the squashed and collapsed
projections. Without this, obfuscating a refusal made it escalate —
`"i do not want to be dead"` was HIGH while `"I don't want to die"` was LOW, which
is an evasion path no honest typist should be penalised for not taking.

The rule originally covered only the projections, because they are the views where
pragmatics genuinely cannot be evaluated. That was too narrow, and the Day 8 probe
found the hole: a rewrite can also *destroy the evidence that dismisses a hit*.
Collapsing repeated letters turns "embarrassment" into "embarasment", which breaks
the benign frame "dying of embarrassment" while leaving "i am dying" intact — so
an idiom the primary had already dismissed was re-admitted from a collapsed
spelling of the same sentence, and a joke scored LOW. Variants keep their clause
boundaries and are still judged on their own merits; they simply cannot overrule
the honest text.

---

## 5. Pragmatics: the hard part

Matching words is easy. Knowing what a sentence *does* is the actual problem. Four
judgements run per occurrence, each reported as a `ctx.*` code so an audit shows
them.

### 5.1 Negation

Three directions, because the languages differ:

- **Backward**, up to 8 tokens, skipping at most 5 transparent words, stopping at
  the first content word. "I **don't** want to die" cancels; "I felt bad yesterday
  and today **not** really" does not cancel something three clauses away. The
  stop-at-content-word rule is what keeps a distant "not" from retracting something
  it says nothing about. Transparent words are function words and hedging
  (`i`, `am`, `to`, `just`, `really`, `feel`, `of`, `intention`, and the
  auxiliaries `have`/`has`/`had`/`do`/`does`/`did` plus `once`) — 64 of them, so
  "I have **no** *intention of* killing myself" is still read as a refusal.
- **Forward**, up to 3 tokens, only for rules tagged with a language, because
  Bengali negates post-verbally: "ami bachte chai **na**" is "I do not want to
  live". An English hit can never be cancelled by a trailing word.
- **Inside the matched span**, again only for language-tagged rules, because Hindi
  puts the cue between stem and auxiliary: "main marna **nahi** chahta".

Two deliberate exclusions:

- `cant` / `cannot` / `couldnt` / `unable` / `hardly` / `barely` are **not**
  negation cues. Inability is not absence. "I can't go on" is a crisis, and
  treating it as a negation would be the single most dangerous bug available here.
- Rules flagged `negation_baked: true` are exempt from the inside-the-span check.
  Their pattern *requires* a cue because the negated phrase is itself the risk:
  "jeena nahi chahta" is "I do not want to LIVE", and "bachte chai na" is "I do
  not want to live". Reading the cue inside such a span as a cancellation would
  invert the rule's meaning exactly backwards. The flag is declared in the data,
  never inferred from the pattern source — an optional cue (`marna (nahi)? chahta`)
  and a required one (`jeena nahi chahta`) are indistinguishable as text, and
  inferring it silently broke the Hindi case it was meant to fix.

A negated hit produces **LOW, not NONE**. Somebody telling a mental-health
companion about death, even in the negative, has said something worth a gentle
check-in.

### 5.2 Figurative speech

Suppressed only with **positive evidence**, and per *occurrence*, never per
message:

- a benign frame in the clause — 11 of them (`this is killing me`, `dying to know`,
  `bored to death`, `killing it`), or
- for rules that opt in via `figurative_subject`, an inanimate subject in the three
  words before the hit — 82 benign subjects (`traffic`, `deadline`, `homework`,
  `meeting`, `phone`, `exam`).

Per-occurrence matters: "this exam is killing me **and I want to die**" suppresses
the first clause and escalates on the second. A message-wide suppression flag would
have let an idiom explain away a disclosure in the same breath.

The opt-in is deliberate. Requiring an inanimate subject is only safe for patterns
whose figurative use is marked by their subject; applying it everywhere would
suppress real hits.

### 5.3 Third person, quotation, and fiction

Detected from 39 markers ("my friend", "she said", "usne kaha", "meri didi"), 8
patterns (including reporting verbs — `texted`, `messaged`, `wrote` were added
after a case showed "he texted me: i am going to end it all" reading as
first-person), and 29 fiction frames ("for my novel", "in a movie",
"hypothetically").

These **cap IMMINENT to HIGH**, because an attempt belonging to somebody else is
answered differently — the reader is a supporter and needs to know what to do.

They never cap `acute_medical`. If somebody has swallowed a bottle, an ambulance is
needed regardless of who the sentence is about.

Quotation is tracked separately (`quoted_only`: every risky hit fell inside quote
marks) so a person reporting what someone else said is not answered as if they had
said it themselves.

### 5.4 First person

33 markers, with relation phrases blanked first so that "my husband threatens to
kill **me**" is correctly first-person. This is what stops a victim of a threat
being redirected to the supporter template — see §6.3.

---

## 6. From level to response

### 6.1 Level resolution

The level is the **maximum** over accepted hits. Then one conditional upgrade:

> IMMINENT requires `intent_plan` in the categories, a level already at HIGH or
> above, no third-person framing, and either a timeframe marker (32 of them:
> "tonight", "aaj raat", "this weekend") or `access_to_means`.

"I have a plan" is HIGH. "I have a plan and I'm doing it tonight" is IMMINENT.
The distinction is evidence, not intensity: the engine will not claim imminence it
cannot point to.

### 6.2 The policy table

Five rows, no branches, no per-user variation:

| level | template | LLM | helplines | emergency | event |
| --- | --- | --- | --- | --- | --- |
| NONE | — | allowed | no | no | no |
| LOW | `check_in.low` | allowed | no | no | no |
| MEDIUM | `check_in.medium` | allowed | yes | no | yes |
| HIGH | `crisis.high` | **blocked** | yes | yes | yes |
| IMMINENT | `crisis.imminent` | **blocked** | yes | yes | yes |

Blocking the LLM below HIGH would turn every sad message into a crisis card, which
is its own harm: a person who is struggling but safe gets an alarm instead of a
conversation, and learns not to be honest next time.

### 6.3 Who the message is addressed to

Third person alone does not select the supporter template. `_about_someone_else`
requires third person **and** (a fiction frame, or quotation, or the absence of the
speaker).

"My husband threatens to kill me" is about somebody else *and* about the speaker.
The speaker is the one who needs the crisis card. Redirecting them to a template
about supporting someone else would be a serious failure dressed up as
sophistication.

### 6.4 The templates

Five per locale, in `app/content/i18n/{en,hi,bn}.json`. Each is field-by-field
rather than one blob of prose — `title`, `body[]`, `helpline_intro`,
`emergency_instruction`, `trusted_person`, `safety_steps[]`, `closing`,
`disclaimer` — so the frontend can render the emergency instruction as a visually
distinct block and a test can assert the wording of each part separately.

The only permitted placeholder is `{emergency_number}`, whitelisted per locale and
interpolated with the region's own number (112 in India, 911 in the US, 999 in the
UK, 000 in Australia). A loader rejects any other placeholder, any template id
missing from a locale, and any locale set without English.

Content requirements, all tested: acknowledge the person; say plainly that Manovia
is a self-help companion, not a person and not the right support for this; point to
the helplines; say what to do in immediate danger; encourage telling one trusted
person; give method-free safety steps; and carry the not-therapy, not-a-crisis-
service disclaimer in the same language.

The tone is warm, plain, short and non-judgemental. No toxic positivity: "the way
this feels can change" is a statement about feelings, not a promise about outcomes,
and the copy never says "everything happens for a reason" or "stay positive".

---

## 7. Privacy: no raw text, structurally

The safety package contains **no logging call at all** — no `structlog`, no
`logger`, no `print`. That is asserted as a grep over the package sources in
`tests/safety/test_no_raw_text.py`, along with no file writes and no cache a caller
could key on a message (the one `lru_cache` here is `load_patterns`, whose
parameters are all keyword-only and which caches our own shipped YAML).

There is no log level to misconfigure and nothing to redact, because nothing is
written.

What crosses the module boundary is a vocabulary:

- `RiskAssessment`: level, categories, rationale codes naming *patterns*
  (`si.want_to_die`), never the words they matched.
- `AssessmentContext`: booleans and counts — `first_person`, `third_person`,
  `quoted`, `fiction_frame`, `timeframe`, `negated_hits`, `figurative_hits`,
  `truncated`, `variants_searched`, and an optional language *tag* from the caller.

A `model_validator` refuses to build an assessment whose rationale code contains a
space, a capital letter or punctuation. That is the guard rail against a future
well-meaning `matched_text` field: the object cannot carry prose, so nobody can add
it by accident.

The API logs a 16-hex SHA-256 fingerprint and a length — enough to correlate two
log lines about one message, useless for reading it. `POST /api/v1/crisis/assess`
writes no `safety_events` row; `policy.record_event` tells the authenticated chat
path (Day 9) when to.

Two tests make the guarantee structural rather than a hunt for leaked substrings:

1. Every string reachable from an assessment must belong to a fixed vocabulary of
   levels, categories, pattern ids and flags — checked across all 218 cases. A leak
   would have to appear as a string, and there is no string that is not vocabulary.
2. The response is a **pure function of the assessment**: round-tripping the
   assessment through JSON and rebuilding the plan must produce a byte-identical
   result. If the plan depended on anything the assessment does not carry, this
   would differ.

Together those say no part of what somebody wrote can reach a response. A canary
token is asserted absent as well, as a direct check.

---

## 8. Safe messaging

`access_to_means` records **that** something is available. It never records how
anything is used, and no output of this module describes a method.

This follows WHO and AFSP guidance for talking about suicide: never describe method
or location, no sensationalism, and do point to where help is available. Talking
about suicide does not increase risk; describing techniques does.

The reason presence is still recorded is practical: proximity to means is a factor
that changes urgency *and* the one a supporter can act on first. The first safety
step in the crisis template is generic means restriction — "move away from anything
you could use to hurt yourself, or ask someone you trust to hold it for now" —
which names no means at all.

`test_no_template_in_any_language_names_a_method` screens all fifteen templates
across three languages against a method vocabulary (pill, rope, hang, gun, knife,
razor, poison, jump, bridge, railway, platform, method, …), matched on **word
boundaries**. A substring screen flagged "can **chang**e" and "be**gun**" and
caught nothing real — two false positives before it found anything.

Test fixtures follow the same rule. `cases.yaml` is synthetic throughout: no real
person's words, no names, no quantities, no locations, no technique.
`test_cases_are_synthetic_and_method_free` enforces that on the file, so the next
person adding a case keeps it that way. One case was rewritten during Day 8 for
exactly this reason — it had named a location and an action.

---

## 9. Known limitations

Stated plainly, because a safety module that claims to be finished is a safety
module nobody is checking.

**The recall figure is circular.** All 218 cases pass, and recall on the 137
HIGH/IMMINENT cases is 137/137. That number means "the engine does what the table
says", not "the engine catches every real crisis". The patterns were tuned against
these cases; measuring on them proves consistency, not coverage. A real recall
measurement needs a labelled corpus of actual messages, which this repo does not
have and should not collect. §9.1 is the only out-of-sample measurement available,
and it is much less flattering than 100%.

### 9.1 What an out-of-table probe actually measured

During Day 8 verification I invented fifteen phrases that appear nowhere in the
case table — five clearly high-risk, five figurative or benign, five negated or
third-person — and ran them once against the already-green engine. Nine of the
fifteen came back as expected. Six did not:

- **Three of the five high-risk phrases returned NONE.** Not a low level, not a
  check-in — nothing. "Drafting the note to leave behind for my sister", "no point
  in waking up again", and a Bengali "I will end this life right here" all scored
  as if they were about homework. First-contact recall on invented high-risk
  phrasing was therefore **2 of 5**, not 137 of 137.
- **One emphatic denial got a crisis card.** "Not once have i thought about
  hurting myself" returned HIGH, because the backward negation scan broke at the
  auxiliary "have" and never reached "not".
- Two smaller errors: an idiom ("dying of embarrassment") scored LOW, and a
  refusal phrased with "anyone" rather than "someone" scored NONE instead of LOW.

All six were fixed at the root cause and are now pinned by 28 regression cases
(`probe-001`…`probe-028`) plus two invariant tests. One of the six turned out to
be my expectation rather than the engine — see `probe-003`.

The honest reading of this is uncomfortable and worth stating: a table that the
patterns were tuned against cannot detect its own holes, and five invented
sentences found three that 190 curated ones had not. Detection coverage is
**bounded by what somebody thought to write down**, and the way to find the next
hole is to keep feeding the engine phrases from outside the table. That is a
standing maintenance task (§10), not something a test suite can discharge.

The probe also found one engine bug rather than a data gap: the repeated-letter
collapse rewrites ordinary words ("embarrassment" → "embarasment"), which broke a
benign frame in the collapsed variant and re-admitted a hit the honest text had
dismissed. `_match` now judges the primary first and lets its verdicts bind every
derived view (§4.1).

**No tense reasoning.** "I used to self harm at school but I stopped two years ago"
gets the crisis card. Inferring past tense reliably is hard, and getting it wrong
in the other direction — reading "I cut myself, then I stopped the bleeding" as
history — is worse. A person who self-harmed in the past gets a warm message and a
phone number. Relapse risk is real and the false positive is cheap.

**Broad risk words stay broad.** A bare mention of dying in a hypothetical question
is HIGH. "My husband threatens to kill me" is recorded under `abuse_disclosure`
*and* `suicidal_ideation`, because narrowing `si.kill_myself` to first-person
subjects would risk missing "I am going to kill me", which people do write. The
level and the reply are identical either way.

**English is over-represented.** 136 English rules against 24 Hindi and 21
Bengali, for a product whose primary audience is Indian. The Indic sets cover the
common romanised forms and native script, and both were checked against real
phrasings, but they are thinner and less battle-tested. Two of the six gaps the
out-of-table probe found in §9.1 were Bengali, from a set one sixth the size of
the English one — which is roughly what that ratio predicts.

**Translations need native-speaker review.** The Hindi and Bengali crisis templates
are written to be warm and plain, and are tested for structure and safe messaging,
but no native speaker has reviewed the wording. This is the highest-value
review task outstanding.

**Helpline data needs re-verification before it ships to anyone in distress.**
Four of the 34 entries carry `needs_verification: true` with a note saying what
could not be confirmed (`in-icall` — conflicting published hours; `in-kiran` — no
reachable ministry page; `in-aasra` — third-party listings disagree;
`au-kids-helpline` — official site not fetched). The UI says so in plain words
rather than presenting a possibly stale number as certain.

**Idioms are a moving target.** Slang, new euphemisms, and code words inside
communities change faster than a YAML file gets reviewed. Detection needs an owner
and a cadence, not just a test suite.

---

## 10. How to change this safely

**Add a phrase.** Add a rule to the right YAML file with a `note` saying why it is
at that level, then add cases to `tests/safety/cases.yaml` — the phrase itself, a
negated form, and a benign sentence that nearly contains it. Run
`pytest tests/safety -q`.

**Broaden an existing rule.** Broaden it in a branch and read what else starts
matching. Two Day 8 broadenings were reverted for this reason: adding `have what i
need` to `access_to_means` escalated "i have what i need for the exam tomorrow",
and adding gerunds to `end_it_all` escalated "i need to finish everything before
friday" and "the medication stopped the pain". A means rule must name a means; an
inflection must attach only to the genuine idiom.

**Change a level.** Levels are a policy decision with a response attached. Read
§6.2 first: moving something to HIGH blocks the LLM for that turn.

**Run an out-of-table probe before trusting a green suite.** This is the practice
§9.1 came out of, and it is the only check that can find a hole the table does not
already cover. Invent ten to fifteen phrases that appear nowhere in
`cases.yaml` — some clearly high-risk, some figurative, some negated or
third-person — run them once, and triage every disagreement as either a data gap
or a wrong expectation *before* recording it. Confirm the phrases really are new
(set-compare against the loaded cases) or the exercise measures nothing. Five such
sentences found three high-risk phrases that returned NONE after 190 curated cases
were already passing, so do this on a cadence and not only at release time.

**Add a language.** Add a patterns file with `language:` set on every rule (that is
what enables post-verbal and in-span negation), add a locale file with all five
template ids, and add cases. The loader fails at startup if a locale is missing a
template, so this cannot half-land.

**Never** weaken a failing safety test to make it pass. Either the pattern data is
wrong or the expectation was written wrong; both are fixed deliberately, and both
leave a note behind. Day 8 is the evidence for why: of the cases that failed
initially, **19** turned out to be expectations that were wrong as written and were
corrected with the reason kept inline, and the rest traced to **four** real engine
defects and data gaps (projections cancelling negation, the collapsed text being a
projection rather than a variant, `is_negated` returning at the first content word,
and Indic negation inside the matched span). Not one assertion was loosened. See the
body of commit `b827133`.

---

## 11. Sources

Helpline data: every entry in `backend/app/content/helplines.json` carries its own
`source_url` and `last_verified` date, retrieved 2026-10-09. Government and
official sources were preferred (Tele-MANAS via PIB and MoHFW, 988 via SAMHSA,
Triple Zero via the Australian Government, 911 via the FCC, 112 via the Australian
communications ministry). Where only a secondary source was reachable, the entry is
flagged `needs_verification` with an explanation rather than published as certain.

Safe messaging guidance:

- WHO, *Preventing suicide: a resource for media professionals* —
  <https://www.who.int/docs/default-source/mental-health/suicide-prevention-journalists.pdf>
- American Foundation for Suicide Prevention, *How to talk safely about suicide* —
  <https://afsp.org/how-to-talk-safely-about-suicide/>
- Harvard T.H. Chan School of Public Health, *How to talk about suicide online* —
  <https://hsph.harvard.edu/research/health-communication/creator-program/creator-resources/how-to-talk-suicide-online-and-support-prevention/>

The three agree on the points this module implements: do not describe method or
location, do not sensationalise, and do point to where help is available.

---

## 12. Day 9: the ML ensemble — a one-way backstop

First version of this section, Day 9 (2026-10-10). Design decisions recorded in
[ADR 0009](adr/0009-safety-ml-ensemble.md); this section describes the system as
built and, as in the rest of this document, states the cost next to each decision.

### 12.1 What changed, and what did not

The rules engine is still the gate. It still runs before anything else, it still
owns the HIGH/IMMINENT deterministic reply, and — this is the point of the whole
design — **nothing that Day 9 added can lower a level the rules found.** What Day
9 adds is a statistical answer to §9's honest sentence: recall is bounded by what
somebody thought to write down. The out-of-table probe showed indirect phrasings
("drafting the note to leave behind for my sister") slipping past a curated
vocabulary; a classifier trained on labelled examples is the instrument that
generalises beyond the vocabulary.

### 12.2 The ratchet

```
message text
    │
    ▼
RuleEngine.assess()            rules level, categories, pattern ids, pragmatics
    │
    ▼
TfidfLogisticClassifier        calibrated P(none..imminent)   [skippable: rules-only]
    │
    ▼
ensemble.combine()             final = max(rules, ML-if-confident)
    │                           ML can RAISE, never LOWER (property-tested)
    ▼
Escalator.plan()               unchanged — policy table, templates, helplines
```

`ensemble.combine()` is the only place the two detectors meet. Every branch keeps
or raises the rules level; a hypothesis property test (800 random predictions ×
random thresholds) asserts `final >= rules level` from the outside. When the ML
path is disabled (`SAFETY_ML_ENABLED=false`), missing its artifact, or throws,
the pipeline is exactly the Day 8 pipeline.

### 12.3 The raise bands

The model contributes two numbers: its top-class calibrated confidence, and its
**crisis mass** `P(high) + P(imminent)`. Three bands, strongest evidence first:

| band | condition | effect | rationale code |
| --- | --- | --- | --- |
| confident top class | `confidence >= SAFETY_ML_MIN_CONFIDENCE` and ML level > rules level | raise to the ML level | `ml.raised` |
| crisis mass | `mass >= SAFETY_ML_CRISIS_MASS_FLOOR` and rules < HIGH | raise to HIGH | `ml.crisis_mass` |
| suspicion | `mass >= SAFETY_ML_SUSPICION_FLOOR` and rules < MEDIUM | treat as MEDIUM (soft check-in) | `ml.uncertain.checkin` |

The crisis-mass band exists because indirect crisis phrasing usually splits its
probability between HIGH and IMMINENT; neither class alone crosses a confidence
threshold while their sum is exactly the evidence being asked about. The suspicion
band is the "uncertain → treat as MEDIUM" policy: respond normally, append the
soft check-in and resources — never a crisis card on a hunch, never silence.

Shipped defaults (chosen by grid search on the dev split only,
`evals/tune_safety_thresholds.py`): `SAFETY_ML_MIN_CONFIDENCE=0.70`,
`SAFETY_ML_CRISIS_MASS_FLOOR=0.30`, `SAFETY_ML_SUSPICION_FLOOR=0.25`. Startup
validation rejects out-of-range values and a suspicion floor above the mass floor
(which would empty the check-in band).

### 12.4 The pragmatics gate

When the rules engine matched risky words but discounted them on positive evidence
— negation hits or a figurative frame — the ML raise bands are blocked for that
message (the gentle check-in band still applies). Without this, the classifier's
n-grams would "see" the same risky words and simply undo the pragmatics: the
classic way a statistical layer talks a system out of a correct NONE. The discount
is evidence; a probability does not get to overrule evidence with vibes.

### 12.5 Response policy at MEDIUM and LOW (unchanged, now ML-reachable)

The Day 8 policy table already did what the task asks; Day 9 makes it reachable
by the model:

- **MEDIUM** — respond normally, append the soft check-in (`check_in.medium`)
  and the region's helplines as resources; the LLM stays allowed; a
  metadata-only `safety_events` row is due.
- **LOW** — the gentle tone: `check_in.low`, no helplines, no event.

### 12.6 What the numbers actually say (honest, and uncomfortable)

Eval set: 572 synthetic cases (en, hi Devanagari, hi romanised, bn), split
202-style into train 340 / dev 116 / test 116, test frozen by hash before any
tuning (see `evals/datasets/manifest.json`). Dev ground truth: 51 HIGH+IMMINENT,
51 NONE/LOW.

| pipeline | dev crisis recall | dev crisis precision |
| --- | --- | --- |
| rules only | 0.333 | 1.000 |
| rules + ML (shipped thresholds) | **1.000** | 0.543 |

The recall target (>= 0.97 on dev) is met. The cost, stated plainly: at these
thresholds **31 of 51 benign dev cases are escalated to a crisis card**, and 7
more to a MEDIUM check-in. The benign and crisis mass distributions overlap badly
(benign median ≈ 0.34, crisis median ≈ 0.54) — a char n-gram model on 340
training examples cannot separate "কাল খেলায় আমাদের দল জিতেছে" from crisis text
with any confidence. The operating point is deliberately recall-first, consistent
with the conservative bias this document has argued for since Day 8 (a false
alarm shows somebody a warm message and a helpline; a miss can be fatal), and the
precision-leaning alternative is documented in `threshold_tuning.md`
(`crisis_mass_floor 0.45–0.50`: recall ≈ 0.80–0.84, crisis-card false alarms cut
by two-thirds) for the day the product decides the alarm fatigue is the bigger
risk.

Three honest caveats about the 1.00 itself:

- **It is tuned on dev.** The test split is the first honest measurement and is
  reported in the Day 9 verification section of PROGRESS.md.
- **The ground truth is one annotator's judgement** (the maintainer) on synthetic
  text. Borderline families (farewell behaviour, "everything is arranged") are
  labelled as a careful human would, which means the eval measures agreement with
  one human, not with the world.
- **The dataset over-represents hard cases on purpose** — that is what it exists
  to stress — so per-level precision on this set understates how the system will
  behave on ordinary traffic, and the false-alarm figures overstate it. Neither
  direction excuses the numbers; they frame them.

### 12.7 Known limitations (Day 9 additions)

- **Calibration at this data size is approximate.** Sigmoid calibration with
  cv=3 on ~340 examples produces noisy probabilities; the masses are good enough
  to order evidence, not to read as frequencies.
- **Indic coverage is thinner than English** in training data (88 bn, 82 hi,
  107 hi-Latn vs 218 en cases) and the model inherits the rules engine's §9
  warning that the Indic sets are less battle-tested.
- **The ML path adds CPU work per message** (~ms for TF-IDF scoring on this
  artifact; measured acceptable under `run_in_threadpool`), and the artifact is
  a committed 2 MB joblib file: reproducible via `evals/train_safety_classifier.py`,
  but opaque like any pickled model. Reviewability lives in the dataset, the
  thresholds, and the eval report — not in the weights.
- **Ground-truth labels differ from engine behaviour by design in places** (a
  past-tense recovery story is LOW truth while the engine — no tense reasoning —
  says HIGH). The eval reports the system against truth, so those over-escalations
  appear in the confusion matrix as the deliberate conservative bias they are.
- **The audit row is anonymous on this endpoint.** `safety_events` rows written
  by the public endpoint have NULL user/session ids; the authenticated chat gate
  will attach both when it lands (Day 10+).
