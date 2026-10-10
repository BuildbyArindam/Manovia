# 0008 - Crisis detection: a deterministic rules engine ahead of every model call

- Status: accepted (Day 8)
- Date: 2026-10-10
- Deciders: Manovia maintainers
- Supersedes: none
- Related: [0002 backend skeleton conventions](0002-backend-skeleton-conventions.md), [0003 data layer](0003-data-layer-models-migrations-and-encryption.md), [0005 frontend skeleton](0005-frontend-skeleton.md), [0006 emotion model](0006-emotion-model.md)

## Context

AGENTS.md rule 1 is not a preference: crisis and self-harm detection runs **before**
any LLM call, and a high-risk message gets a deterministic, pre-written response.
Everything else in this product may degrade, retry, or fall back to a cheaper
model. This may not.

That rules out the obvious design. A classifier — the Day 6 emotion model, or an
LLM asked "is this person at risk?" — is a probability, and a probability is not
something to put between a person in crisis and a helpline number. It also fails
in the direction that matters: a model that has never seen romanised Bengali will
score "ami bachte chai na" as ordinary sadness, and there is no way to audit why.

So Day 8 builds a rules engine: normalisation, a curated pattern vocabulary, and
explicit pragmatic reasoning about negation, quotation and figurative speech. It
has to be good enough to be trusted alone, because for the foreseeable future it
is.

Three constraints shaped every decision below.

- **Conservative bias.** A false positive shows a warm message and a phone number
  to someone who was having a bad day. A false negative is somebody who is not
  here any more. Those are not symmetric, and the engine is built accordingly.
- **Auditability.** When this fires wrongly — and it will — a reviewer has to be
  able to answer "why" in seconds, without reading anybody's message.
- **No raw text.** The module sees the most sensitive text in the product. It
  stores none of it.

## Decisions

### 1. Rules first, and the model is not allowed to disagree

`RuleEngine.assess(text)` runs before any provider call. At HIGH and IMMINENT the
policy sets `allow_llm=False` and `deterministic_reply=True`: the turn is answered
by a template from `app/content/i18n/*.json` and the model is never invoked. The
engine cannot be overridden by a model, by a user setting, or by a prompt.

The corollary is that the model never gets a veto it should not have. A completion
may be *added* at LOW and MEDIUM; it may not *replace* a crisis reply.

### 2. Patterns are data, not code

All 178 detection rules live in `app/content/safety/patterns_*.yaml` — core,
euphemisms, Indic, and a context file holding the pragmatics vocabulary (leet
map, misspellings, negation cues, transparent words, third-person markers,
fiction frames, benign subjects, timeframe markers). The Python contains the
machinery: normalisation, matching, suppression, level resolution.

This is the same call as ADR 0006 putting the checkpoint name in one env var: the
people who should be able to improve detection are not only the people who can
read a regex engine. Adding a phrase is a data edit with a test, not a code
change with a review of control flow.

Each rule declares `id`, `category`, `level`, `kind` (phrase or regex), `value`,
and the flags that make it safe to run: `negation_sensitive`, `figurative_prone`,
`figurative_subject`, `language`, `script`, `negation_baked`, plus a `note` saying
why it is at that level. The loader rejects unknown keys, uppercase values
(matching happens on normalised text), and any file that fails to compile — a typo
in the data is a startup failure, not a silent gap.

### 3. Every projection is searched; the honest one decides the pragmatics

Normalisation produces several views of one message and searches all of them:
the primary form (apostrophes deleted, casefolded, punctuation to spaces with
Indic combining marks preserved), a leet-decoded form, a spelling-corrected form,
a collapsed form (`diiiie` → `die`), and a whitespace-stripped form
(`wanttodie`). Obfuscation must not be easier to get past than honest typing.

But the projections have limits, and the engine respects them. In
`idonotwanttobedead` the word "not" is no longer a token, so negation is
invisible; a rule judged negated in the honest text stays negated in the trick
projection. **A projection can add a hit the honest text hid. It can never cancel
one.** Getting this wrong would have made obfuscation an escalation path.

The collapsed and spelling-corrected text is a *variant*, not a projection, so it
keeps its clause boundaries and therefore keeps its pragmatics. That distinction
is load-bearing: it is why `kiiiillll meee` collapses to `kil me`, gets spelling
corrected to `kill me`, and is then judged with full context.

### 4. Five levels in the module, four tiers in the database

`RiskLevel` is `NONE < LOW < MEDIUM < HIGH < IMMINENT`, an `IntEnum` so ordering
is comparison rather than a lookup table, and the level of a message is the
**maximum** over accepted hits. IMMINENT is then a conditional upgrade, never a
rule's own verdict alone: it needs `intent_plan` present, a level already at HIGH
or above, no third-person framing, and either a timeframe marker or access to
means. "I have a plan" is HIGH; "I have a plan and I'm doing it tonight" is
IMMINENT.

The `safety_events` table predates this module and stores a four-tier
`StoredRiskLevel` with a database CHECK constraint. Rather than migrate a
constraint for no operational gain, the mapping lives in `RiskLevel.to_stored()`:

| `RiskLevel` | stored tier | why |
| --- | --- | --- |
| NONE | `none` | nothing to record |
| LOW | `caution` | worth a check-in, not an audit trail of its own |
| MEDIUM | `elevated` | its own tier: helplines offered, event recorded |
| HIGH | `crisis` | deterministic reply, no model |
| IMMINENT | `crisis` | same response as HIGH, more urgent wording |

Only IMMINENT folds into another tier, and it folds into the one that already
triggers the deterministic reply. Nothing that changes the *response* is lost by
the collapse; the five-level distinction survives on the wire (`level_code`) and
in the assessment, which is where a reviewer reads it.

The database enum is deliberately untouched. Two vocabularies with an explicit,
tested mapping beat one vocabulary changed under a CHECK constraint that a
migration would have to rewrite.

### 5. Pragmatics are explicit, and each one can only ever reduce certainty

Four judgements run over the clause containing a hit, and each is reported as a
`ctx.*` rationale code so it is visible in an audit:

- **Negation.** Backward up to 8 tokens, skipping at most 5 transparent words and
  stopping at the first content word — a distant "not" must not cancel something
  it says nothing about. Forward, and *inside* the matched span, only for
  language-tagged rules, because Bengali negates post-verbally ("ami bachte chai
  **na**") and Hindi puts the cue between stem and auxiliary ("main marna **nahi**
  chahta"). `cant`/`cannot` are deliberately **not** cues: inability is not
  absence, and "I can't go on" is a crisis, not a refusal.
- **Third person and quotation.** Cap IMMINENT to HIGH, because an attempt
  belonging to somebody else is answered differently — but never cap an acute
  medical emergency, since an ambulance is needed regardless of who the sentence
  is about.
- **Figurative.** Suppressed per *occurrence*, never per message, and only with
  positive evidence: a benign frame in the clause ("this traffic is killing me"),
  or, for rules that opt in via `figurative_subject`, an inanimate subject in the
  three words before the hit. "This exam is killing me **and I want to die**"
  escalates on the second clause.
- **`negation_baked`.** A rule whose pattern *requires* a cue is the risk itself:
  "jeena nahi chahta" is "I do not want to LIVE". Such rules are exempt from the
  inside-the-span check. The flag is declared in the data, never inferred from the
  pattern source — an optional cue and a required one are indistinguishable as
  text, and inferring it silently broke the Hindi case it was meant to fix.

A negated hit does not produce NONE; it produces LOW. Somebody telling a
mental-health companion about death, even in the negative, has said something.

### 6. No raw text, at any level, anywhere

The safety package contains **no logging call at all** — no `structlog`, no
`logger`, no `print`. There is no log level to misconfigure and nothing to redact.
`tests/safety/test_no_raw_text.py` asserts that as a grep over the package
sources, along with no file writes and no cache a caller could key on a message.

What crosses the boundary instead is a vocabulary. `RiskAssessment` carries a
level, categories, and rationale codes that name *patterns* (`si.want_to_die`),
never the words they matched; plus `AssessmentContext`, which is flags and counts.
A model validator refuses to build an assessment whose rationale code contains a
space, a capital letter or punctuation — the guard rail against a future
well-meaning `matched_text` field.

The API logs a 16-hex SHA-256 fingerprint and a length, which is enough to
correlate two log lines about one message and useless for reading it. The
response is proven a pure function of the assessment by round-tripping it through
JSON and requiring a byte-identical plan.

### 7. Safe messaging: point to help, never to a means

`access_to_means` records **that** something is available, because proximity
changes urgency and because it is the factor a supporter can act on first. It
never records how anything is used. No pattern, template, category name, API
response or test fixture describes a method, and
`test_no_template_in_any_language_names_a_method` screens all fifteen templates
across three languages against a method vocabulary, matched on word boundaries —
a substring screen flags "can **chang**e" and "be**gun**" and catches nothing
real. This follows WHO and AFSP guidance for talking about suicide.

The HIGH and IMMINENT templates acknowledge the person, say plainly that Manovia
is a self-help companion and not a person or the right support for this, point to
the region's helplines, give an emergency instruction with the local number
interpolated, encourage telling one trusted person, and list method-free safety
steps (the first being generic means restriction: move away from anything you
could use to hurt yourself, or ask someone to hold it). A supporter asking about
somebody else gets a different template, addressed to them.

### 8. Helplines are verified content with a date and an honest flag

`app/content/helplines.json` is the only place helpline data lives. Every entry
carries `source_url` and `last_verified`; the file is validated against a published
JSON Schema and against structural invariants at load (every declared region has
an emergency number, no duplicate ids, every resource has at least one way to make
contact). Entries that could not be fully confirmed carry
`needs_verification: true` and a `verification_note` saying what disagreed — a
possibly stale number presented as certain is worse than one flagged as
uncertain, and the UI says so in plain words.

Regions are resolved with a fallback to `DEFAULT`, and `DEFAULT` entries are
appended to every region so a thin region never looks empty. A region's own
emergency number wins: a US card showing 112 above 911 is worse than showing 911
alone.

### 9. Known costs of the conservative bias, accepted deliberately

Recorded so nobody has to rediscover them:

- **No tense reasoning.** "I used to self harm at school but I stopped two years
  ago" gets the crisis card. Inferring past tense reliably is hard, and getting it
  wrong in the other direction is worse.
- **Broad risk words stay broad.** A bare mention of dying in a hypothetical
  question is HIGH. The reply is a warm message and a phone number, not an
  accusation.
- **Imprecise categories under threat.** "My husband threatens to kill me" is
  recorded under `abuse_disclosure` *and* `suicidal_ideation`, because narrowing
  `si.kill_myself` to first-person subjects would risk missing "I am going to kill
  me". The level and the reply are identical either way.
- **Translations need native-speaker review.** The Hindi and Bengali templates are
  written to be warm and plain, but no native speaker has reviewed them yet.

## Consequences

**Positive.** Detection is deterministic, fast (≈0.25 ms per message), fully
offline, and explainable from pattern ids alone. Improving it is mostly a data
edit with a test. Nothing sensitive is stored, so the module cannot become a
liability in a breach. Romanised and native Hindi and Bengali are first-class,
which matters for the primary audience.

**Negative.** Recall is bounded by what somebody thought to write down: a phrasing
nobody anticipated is missed, and there is no statistical backstop. The pattern set
needs continuous curation, and every broadening risks a false positive that shows
a helpline card to somebody having an ordinary bad day. English idioms are
over-represented relative to the Indic ones, which are the ones the product most
needs.

**Follow-ups.** The engine is not yet wired into the chat path — `POST
/api/v1/crisis/assess` exists and is rule-only, and `policy.record_event` tells
the authenticated chat path (Day 9) when to write a metadata-only `safety_events`
row. The output-safety check on LLM completions (AGENTS.md rule 4) is still to
come. A recall measurement against a labelled real-world corpus, rather than our
own 190 synthetic cases, is the obvious next validation step and cannot be done
honestly from inside this repo.
