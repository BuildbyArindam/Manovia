# Top 25 interview questions about Manovia — with honest answers

> Ground rules for every answer: numbers come from the repository's own
> reports; limits are stated before anyone asks; no clinical claims, ever.

## Design and safety

**1. Why rules-first instead of letting the LLM handle crisis messages?**
Because the failure modes are asymmetric and the worst one is irreversible.
A deterministic rules engine is reviewable line-by-line, testable offline,
identical on every run, and cannot be prompt-injected. An LLM is none of
those things. So crisis detection runs *before* any model call, and at high
risk the model is never called — the user gets pre-written, reviewable copy
with helplines. The LLM is the least-trusted component in the system: it
only ever handles the residual, lower-risk conversation, and even then its
output passes a deterministic guard. The measured cost of this stance is
real and I can name it: precision 0.543 on the dev split — roughly three in
five crisis escalations are false alarms.

**2. What's the biggest safety trade-off you consciously made?**
Recall over precision. The ensemble reaches 1.000 HIGH+IMMINENT recall on
the dev split, and the price is that 31 of 51 benign look-alike cases
(recovery stories, third-person news) get escalated to a crisis card. I
chose that because a missed crisis is a potentially unrecoverable harm and
an unnecessary crisis card is an annoyance — but I also documented the
annoyance honestly: over-escalation erodes trust and can desensitise users
to the card. It's measured (9 benign red-team controls escalate with ML on),
tracked as a known issue, and the retrain with hard negatives is the first
v2 item.

**3. Why can the ML backstop raise risk but never lower it?**
Monotonicity makes the system auditable: the rules engine's decision is a
floor, so no model update, threshold tweak or adversarial input can ever
suppress a detection the rules made. If the ML is wrong upward, a human gets
a crisis card they didn't need. If it could be wrong downward, a person in
crisis could get small talk. One-way composition means the worst case of the
learned component is bounded.

**4. What happens on an LLM outage or a hostile provider response?**
The provider chain ends in a canned local reply, so the app degrades to
something safe rather than failing open. Every provider reply — including
streamed ones — passes the output guard, which blocks diagnosis language,
medication dosing, method or quantity details, identity claims, jailbreak
and prompt-leak content. In the worst-case jailbreak run, with the fake
provider *scripted to comply with the attack*, 10/10 conversations still
ended safely because the guard, not the model, has the last word.

**5. How do you stop prompt injection?**
Several layers, honestly imperfect: user text is clearly delimited for the
model; conversation memory, retrieved knowledge and the knowledge-base
loader pass an injection tripwire; the system prompt is versioned; and the
output guard catches prompt-leak and jailbreak-shaped replies. I'd say in
the interview exactly what the report says: the tripwire is regex-based and
paraphrasable — it raises the cost of injection, it does not make it
impossible. The guard on the *output* is the stronger invariant.

**6. The session risk accumulator — what problem does it solve?**
Single-message classifiers miss slow escalation: eight messages that are
individually MEDIUM can be a crisis in aggregate. The red team proved it —
a scripted 8-message decline never escalated. The accumulator tracks risk
across the session, and the same script now escalates at turn 6, for both
saved and ephemeral sessions, with a control conversation that never
escalates pinned in tests.

**7. Why do you never log message text, and how is that enforced?**
Because logs leak — to crash reporters, to shipped log files, to anyone with
operator access — and this is the one domain where text is maximally
sensitive. The structured logger drops raw-text fields before writing;
Sentry events pass a scrubber that drops bodies, queries and cookies and
redacts free text; uvicorn's access log is off so query strings never land
on disk. It's enforced by tests, including an end-to-end one, and a Day 28
decision removed a GET streaming route *because* it put the message in a URL.

## Evaluation and evidence

**8. Your recall is 1.000. Do you believe it?**
On that dataset, yes — it's deterministic and re-runnable. About the world,
no, and I won't present it that way. It's 51 of 51 high-risk cases on a
synthetic, author-written dev split. Real people in crisis use dialects,
typos, novel euphemisms and multi-turn context the patterns have never seen.
The number demonstrates regression discipline — weakening one pattern fails
CI — not clinical sensitivity. That distinction is written into the eval
report itself.

**9. What are the main limits of your evaluation?**
Four I'd volunteer: (1) all data is synthetic and partly authored by the
same person who wrote the rules — a circularity risk; (2) the frozen test
split has been scored more than once, so it's no longer pristinely held
out; (3) the emotion model collapses under domain shift — macro-F1 0.992
in-domain versus 0.011 on implicit/somatic phrasing — which I report as a
headline failure, not a footnote; (4) response-pipeline results use a
deterministic fake provider, so real-provider behaviour is unmeasured in CI.

**10. Why bootstrap confidence intervals in a CI pipeline?**
Because point estimates on n=116 lie. A recall of 1.000 with a CI of
[1.000, 1.000] and a precision of 0.543 with [0.441, 0.645] tell you very
different things about how much to trust each number. Gates compare against
thresholds, but the intervals keep *me* honest about what a change actually
proved — and they cost one deterministic, seeded resampling step.

**11. Tell me about red-teaming your own system.**
I wrote 150 adversarial cases before knowing the answers: euphemisms,
slang, six languages, confusable characters, fiction framing, prompt
injection, IDOR, token abuse, jailbreaks. Baseline: 82/140. The most
humbling finding: the rules engine silently stopped reading at 4,000
characters, so a risk phrase at the end of a long journal entry was
invisible. Every fix got a regression test; ten new cases were added as
findings emerged; final: 150/150, IDOR 18/18. The honest caveat: a suite I
wrote can't surprise me the way a real adversary can — that's why the cases
and harness are committed, so others can extend them.

**12. How would you know if a change made safety worse?**
Mechanically: the eval gate diffs recall per language against the previous
report and fails on any regression; the red-team suite runs in pytest; the
coverage gate requires 95% branch coverage on every safety module so
untested safety branches can't merge; and I proved the gate works by
deliberately commenting out one HIGH pattern — recall dropped 0.039 and CI
exited 1.

## Privacy

**13. Walk me through the privacy design.**
Data minimisation first: guest-first accounts, no identifiers required.
Message text, journal entries and mood notes are encrypted at rest per
field; the journal uses envelope encryption with per-entry keys. PII is
redacted before any text reaches a provider, and user IDs never enter a
prompt — there's a test asserting identifying text can't reach the provider.
Users can export everything and delete everything, with their own retention
window enforced by a real job. Logs and Sentry are scrubbed by construction.
The threat model is written down, with the residual risks stated.

**14. Why field-level encryption when the database is already access-controlled?**
Defence against the realistic failure, which is a leaked backup or a
misconfigured replica, not a cracked cipher. Encrypting the sensitive
columns means a database snapshot alone doesn't yield message text; key
rotation is scripted. Residual honesty: the key and the data are handled by
the same application process, so a fully compromised app server still wins —
that's in the threat model, not hidden.

**15. What privacy choice would you defend hardest?**
Never sending identifiers to the LLM and never logging raw text — because
they're *structural*. A policy can be violated by one sloppy log line; a
redaction layer plus tests that fail on violation make the safe path the
default path. Also the decision that exports are rate-limited per user (the
red team showed export was an exfiltration amplifier).

## Engineering

**16. Why FastAPI + React rather than a simpler stack?**
The backend needed async streaming, a strict typed boundary (Pydantic v2),
and first-class testability; FastAPI gives all three and the OpenAPI schema
becomes the committed, drift-tested API reference. React + TypeScript +
Vite earns its complexity in an accessible, installable PWA with offline
behaviour. Honest counterpoint: for the backend alone, Django would have
given me admin and auth for free; I preferred explicitness where the safety
pipeline lives.

**17. How do you test an LLM app offline?**
Every external dependency sits behind an interface with a fake: the fake
provider is deterministic and scriptable — including scripting it to behave
*maliciously*, which is how the worst-case jailbreak run works. The entire
suite — thousands of backend tests, the eval harness, the red-team suite —
passes with no network. The cost, stated plainly: real-provider behaviour is
unmeasured in CI, and I keep that in the known issues rather than letting
the green badge imply otherwise.

**18. What's your deployment story, and what's deliberately not done?**
Gunicorn-managed uvicorn workers, a production compose overlay that refuses
to boot without real secrets, Prometheus metrics with closed-set labels,
scrubbed opt-in Sentry, nightly backup scripts with a verified restore
drill, and a tagged-release workflow that round-trips migrations on
Postgres before publishing images. Deliberately not done: an actual public
deployment — because the gate for serving real users is clinical review,
not infrastructure.

**19. Biggest bug you found, and what it taught you.**
The 4,000-character truncation in risk assessment. The lesson: safety
boundaries fail at their *edges* — truncation, normalisation, encoding,
language switching — not in the happy path. It changed how I write tests: I
now test the boundary explicitly (phrase *at* the window edge, past it,
split across chunks) and the fix was windowed assessment, not a bigger
constant.

**20. You wrote ~4,000 backend tests in 30 days. Were they worth it?**
The alternative in this domain is shipping unverified safety claims. The
suite is what let me refactor the rules engine mid-red-team without fear,
and the coverage *gates* (branch-level on safety modules) caught untested
decision branches the statement numbers hid. I'd also admit the Day 28
finding: two earlier "gate PASS" claims were wrong for the tree they
described, which is exactly why gates should run in CI and not in a human's
memory — running the branch gate in CI is still an open item.

## Product honesty and the future

**21. What would you do differently with a clinical partner?**
Nearly everything downstream of detection. Concretely: have them review the
crisis copy, escalation thresholds, helpline presentation and screening
flows against the checklist already in `docs/clinical-review.md`; replace my
authored risk tiers with clinically grounded criteria (e.g. C-SSRS-informed
categories); design the over-escalation trade-off with them — a clinician
may *not* want 3-in-5 false crisis cards; build an evaluation set reviewed
by clinicians instead of authored by me; define an adverse-event process
with a named clinical owner; and only then discuss any live pilot, under
ethics approval, with consenting participants.

**22. What will you NOT claim about this project?**
That it detects suicide risk in the real world; that it's validated,
clinically reviewed, or evidence-based care; that it helps users (there are
none); that 1.000 recall means zero missed crises; that the Hindi/Bengali
content is correct (native review is pending); that it's production-ready
for vulnerable users. The UI itself states "Portfolio demo — not a clinical
service." I treat the refusal to over-claim as part of the engineering: the
README, model card and release notes all carry the same limits.

**23. Isn't building a mental-health app without clinicians irresponsible?**
Building one and *shipping it to vulnerable users* without clinicians would
be. Building one as a labelled portfolio demonstration, with the clinical
review as an explicit unpassed gate, deterministic helpline-first crisis
behaviour, and no acquisition of real users — I'd argue that's the
responsible version of learning in this domain. The riskiest alternative is
the common one: shipping a thin GPT wrapper with a disclaimer. Manovia is
largely a catalogue of the engineering that wrapper is missing.

**24. What's v2.0?**
Days 31–49 of the plan: the deep-learning and futuristic tracks — starting
with retraining the safety classifier on the red team's hard negatives to
attack the 0.543 precision without losing ensemble recall, then learned
components (multilingual emotion that survives domain shift, semantic
retrieval) behind the same interfaces with the same offline gates. Beyond
v2.0 sit the obligations, not features: clinician review, validated
translations, user research only with ethics approval, an accessibility
audit by real assistive-technology users, push reminders that leak nothing
on a lock screen, and a native wrapper. The order matters: obligations
before growth.

**25. If one thing in this repo should impress me, what is it?**
Not a feature — a habit. Every claim has a file: the eval numbers have
reports with confidence intervals, the red-team score has a committed
harness anyone can re-run, the "gate PASS" claims were themselves audited
and two wrong ones were corrected in writing, and the product's own footer
tells users what it isn't. In a domain where the temptation is to demo the
best case, the repository is built to make the worst case checkable.
