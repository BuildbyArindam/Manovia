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
    │  match 178 rules × every projection
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

178 rules across four YAML files in `app/content/safety/`:

| file | rules | what it holds |
| --- | --- | --- |
| `patterns_core.yaml` | direct statements | the eight risk categories in plain English |
| `patterns_euphemisms.yaml` | indirect language | "check out for good", "already gone", spacing and letter tricks |
| `patterns_indic.yaml` | Hindi and Bengali | romanised and native script |
| `patterns_context.yaml` | 0 rules | the pragmatics vocabulary (§5) |

By level: 113 high, 41 medium, 12 low, 12 imminent. By language: 135 English, 24
Hindi, 19 Bengali. By kind: 155 regex, 23 phrase.

By category:

| category | rules | notes |
| --- | --- | --- |
| `suicidal_ideation` | 73 | the largest set, and the one most exposed to figurative speech |
| `severe_hopelessness` | 30 | mostly medium; high when it implies absence ("no reason to live") |
| `intent_plan` | 21 | what separates a wish from an arrangement |
| `self_harm` | 16 | |
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

**A projection can add a hit the honest text hid. It can never cancel one.**

Concretely: a rule judged negated or figurative in any variant is skipped
entirely in the projections. Without this, obfuscating a refusal made it escalate
— `"i do not want to be dead"` was HIGH while `"I don't want to die"` was LOW,
which is an evasion path no honest typist should be penalised for not taking.

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
  (`i`, `am`, `to`, `just`, `really`, `feel`, `of`, `intention`) — 57 of them, so
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
   levels, categories, pattern ids and flags — checked across all 190 cases. A leak
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

**The recall figure is circular.** All 190 cases pass, and recall on the 127
HIGH/IMMINENT cases is 127/127. That number means "the engine does what the table
says", not "the engine catches every real crisis". The patterns were tuned against
these cases; measuring on them proves consistency, not coverage. A real recall
measurement needs a labelled corpus of actual messages, which this repo does not
have and should not collect.

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

**English is over-represented.** 135 English rules against 24 Hindi and 19
Bengali, for a product whose primary audience is Indian. The Indic sets cover the
common romanised forms and native script, and both were checked against real
phrasings, but they are thinner and less battle-tested.

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
