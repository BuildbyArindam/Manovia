# Manovia roadmap

v1.0.0 (2026-10-13) closes the 30-day build: the rules-first safety pipeline,
the self-help tools, privacy controls, three languages, the evaluation
harness, the red-team suite, and launch positioning ("Beta — portfolio demo,
not a clinical service"). What ships next, in order:

## v2.0 — Days 31–49 of the plan (deep learning + futuristic tracks)

Days 31–49 of the build plan are the deep-learning and futuristic tracks, and
they are v2.0, not patches to v1.x. Candidate work in those tracks (each item
lands only with the same offline-testable, rules-first discipline as v1):

- Retraining the safety classifier (`crisis_v1`) with the hard negatives the
  red team produced, targeting the measured over-escalation (dev precision
  0.543; 9 benign red-team controls escalated) without giving up ensemble
  recall. This is the first item carried over from the v1 parking lot.
- Learned models where v1 used lexicons and templates: better multilingual
  emotion understanding (the domain-shift collapse, 0.992 → 0.011 macro-F1,
  is the motivating number), semantic retrieval over the curated knowledge
  base, and richer insight generation — all behind the existing provider
  interfaces with fakes for tests.
- The "futuristic" experiments from the plan, evaluated with the same harness
  and thresholds before any of them face a user.

## Beyond v2.0 — before Manovia could serve real users

These are not features; they are obligations, and most need people the
repository does not contain:

1. **Clinician review** of crisis copy, helplines, the knowledge base,
   exercise instructions, screening presentation and the escalation policy
   ([docs/clinical-review.md](clinical-review.md) is the checklist; it has no
   sign-offs).
2. **Validated translations**: native-speaker review of every Hindi and
   Bengali string and of the non-English crisis rules
   ([translations/STATUS.md](../translations/STATUS.md)).
3. **User research with consenting students**, only with ethics approval
   (IRB/REC or institutional equivalent), informed consent, and a crisis
   protocol agreed with the reviewing body first.
4. **Push reminders** for check-ins and plans — opt-in, quiet-hours aware,
   and content-free on the lock screen (no mood or risk data in a
   notification).
5. **Accessibility audit by real users** of assistive technology, not only
   automated axe/Lighthouse runs.
6. **Native mobile wrapper** (the PWA already installs; a store-distributed
   wrapper adds review obligations, which is why it comes after the audits).

Also carried from the v1 known issues: running the branch-coverage gate in
CI, a first real deployment per [docs/deploy.md](deploy.md), a shared
session/limiter store so more than one worker can run, and phone verification
of every helpline entry.
