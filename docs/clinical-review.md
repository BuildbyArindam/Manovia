# Clinical review checklist — before public launch

**Status: not started.** Every box below is unchecked, and no reviewer is named.
This is the list of things a licensed professional must look at before Manovia is
offered to the public. Completing it does not make Manovia a medical device or a
crisis service. It makes the content we ship defensible.

Read [docs/model-card.md](model-card.md) §7 (failure modes) and §9 (open questions)
first. They explain why several items below exist.

## How to use this list

1. Each item has an ID, the **exact file or key** it covers, what to decide, and
   whether it blocks launch.
   - **BLOCKER**: do not launch to the public until it is resolved.
   - **BEFORE-LAUNCH**: resolve before the first public release, but it does not
     stop an internal or closed pilot.
   - **AFTER-LAUNCH**: track it; it does not block release.
2. The reviewer writes a decision next to the item (*accept*, *change to*, or
   *remove*), initials it and dates it. Changes are made in a pull request that
   cites the item ID and gets a second review.
3. A change to any file listed here needs this checklist re-checked for that item.
4. Items that are not clinical (translation, legal) are marked and assigned to
   the right person.

Where this list says "the team" it means the repository maintainers.

---

## A. Crisis messages (copy)

Source: `backend/app/content/i18n/{en,hi,bn}.json`, keys under `templates.crisis.*`
and `templates.check_in.*`. Structure: `title`, `body`, `helpline_intro`,
`emergency_instruction` (with `{emergency_number}`), `trusted_person`,
`safety_steps`, `closing`, `disclaimer`. Served by `backend/app/content/i18n.py`.

- [ ] **A-1 (BLOCKER) Hindi crisis copy.** Read every `hi` value under
  `templates.crisis.*` and `templates.check_in.*` line by line. Check that the wording
  is natural, is not alarming, does not imply Manovia can act, and is correct for a
  person in distress. Current status: AI-drafted, not native-reviewed
  ([translations/STATUS.md](../translations/STATUS.md), `crisis` row). Reviewer and date: ______
- [ ] **A-2 (BLOCKER) Bengali crisis copy.** The same check for every `bn` value. Current
  status: AI-drafted. Reviewer and date: ______
- [ ] **A-3 (BLOCKER) English crisis copy.** Check `templates.crisis.high`,
  `templates.crisis.imminent` and `templates.crisis.about_someone_else`. In particular,
  check that the wording on method and means is safe. Current status: team-written.
  Reviewer and date: ______
- [ ] **A-4 (BLOCKER) `safety_steps`.** In all three locales, decide whether each step
  (for example moving away from things a person could use to hurt themselves, or handing
  them to someone else) matches recognised safety-planning practice, and whether any step
  should be removed or reworded. Reviewer and date: ______
- [ ] **A-5 (BLOCKER) `about_someone_else` template.** Confirm that the framing (a person
  worried about another person) is not dismissive of a speaker who is actually at risk. The
  policy and the reason are in [docs/safety-design.md](safety-design.md) §6.3. Reviewer and date: ______
- [ ] **A-6 (BEFORE-LAUNCH) `disclaimer` in every crisis and check-in template.** Confirm
  the wording "not therapy, cannot diagnose, not a crisis service" in every locale. Reviewer and date: ______
- [ ] **A-7 (BEFORE-LAUNCH) `check_in.low` and `check_in.medium`.** Confirm the MEDIUM
  copy is supportive and does not escalate tone. Confirm that helplines appearing at MEDIUM
  is the intended behaviour (policy P-2). Reviewer and date: ______
- [ ] **A-8 (BEFORE-LAUNCH) Output-guard fallback replies.** `backend/app/content/safety/output_patterns.yaml`,
  `safe_replies` (en, hi, bn) and `trimmed_closings`. These are the words a person sees
  when a model reply is rejected. Confirm they are warm, plain, and do not imply a
  diagnosis or medication advice. Reviewer and date: ______

## B. Escalation policy

Sources: [docs/safety-design.md](safety-design.md) §6.2 (the policy table),
`backend/app/services/safety/escalation.py`, `backend/app/services/safety/ensemble.py`,
`evals/thresholds.yaml`.

- [ ] **P-1 (BLOCKER) The policy table.** Confirm that HIGH and IMMINENT block the LLM
  and show helplines, emergency number and the crisis card; that NONE and LOW allow the
  LLM with no helplines; and that MEDIUM allows the LLM and adds helplines and a check-in.
  Reviewer and date: ______
- [ ] **P-2 (BEFORE-LAUNCH) MEDIUM shows helplines.** Decide whether helplines should show
  at MEDIUM, or only when the person asks. The current design shows them. Reviewer and date: ______
- [ ] **P-3 (BLOCKER) Over-escalation budget.** On the dev split about 46% of escalations
  are not crises (precision 0.543; model card §5.1 and §9). Decide, with a documented
  rationale, whether that cost is acceptable for a self-help product, and what false-alarm
  rate would trigger a threshold review. Reviewer and date: ______
- [ ] **P-4 (BLOCKER) Thresholds.** `evals/thresholds.yaml`: `min_confidence 0.70`,
  `crisis_mass_floor 0.30`, `suspicion_floor 0.25` (`backend/app/core/config.py`). These
  were chosen on the dev split. Approve them, or require a new tuning run on a
  clinician-labelled set. Reviewer and date: ______
- [ ] **P-5 (BLOCKER) Pragmatics gate.** Review how negation and figurative frames
  reduce a risk level (`backend/app/services/safety/rules.py`, `docs/safety-design.md`
  §5). Confirm that a discount on positive evidence cannot hide a real risk. Reviewer and date: ______
- [ ] **P-6 (BLOCKER) IMMINENT rule.** The timeframe marker list and the means check
  (`docs/safety-design.md` §6.1). Confirm the list is adequate and that treating HIGH and
  IMMINENT identically in policy is acceptable. Reviewer and date: ______
- [ ] **P-7 (BLOCKER) Minors.** The product has **no age policy and no age gate**. Decide
  whether minors may use it, how to tell, and which helplines apply (for example the
  Childline and Kids Helpline entries in `helplines.json`). Reviewer and date: ______
- [ ] **P-8 (AFTER-LAUNCH) Risk across many turns.** Escalation is per message, and the
  memory summary is advisory only. Decide whether a multi-turn trigger is needed (model
  card F10). Reviewer and date: ______
- [ ] **P-9 (BEFORE-LAUNCH) Pattern vocabulary.** Review the HIGH and IMMINENT patterns in
  `backend/app/content/safety/patterns_core.yaml`, `patterns_context.yaml`,
  `patterns_euphemisms.yaml` and `patterns_indic.yaml`. Look for missing expressions
  and for expressions that are not crisis language. Reviewer and date: ______
- [ ] **P-10 (BEFORE-LAUNCH) Item-9 policy.** A positive PHQ-9 item 9 shows support before
  the score. The daily plan switches to support-only while a positive item-9 answer falls
  within `LOOKBACK_DAYS = 14` (`backend/app/services/plan/daily_plan.py`;
  `docs/adr/0021-daily-plan-and-week-3-review.md`). Confirm the 14-day window and the wording. Reviewer and date: ______

## C. Helpline data

Source: `backend/app/content/helplines.json` (`last_verified` **2026-10-09**), schema
`helplines.schema.json`. Each entry has `source_url`, `last_verified` and
`needs_verification`. Regions: IN, US, GB, AU, CA, DEFAULT.

- [ ] **H-1 (BLOCKER) Four entries flagged `needs_verification: true`.**
  - `in-icall` (iCall, 9152987821)
  - `in-kiran` (KIRAN, 18005990019)
  - `in-aasra` (Aasra, +919820466726)
  - `au-kids-helpline` (1800551800)

  For each: confirm the number, hours, languages and text keyword against the
  organisation's own page, or remove the entry. Reviewer and date: ______
- [ ] **H-2 (BLOCKER) Emergency numbers.** `in-emergency-112`, `us-emergency-911`,
  `gb-emergency-999`, `au-emergency-000`, `ca-emergency-911`, `default-emergency-112`.
  Confirm each against the official source in `source_url`. Reviewer and date: ______
- [ ] **H-3 (BLOCKER) Re-verify every entry.** All entries were last checked on
  2026-10-09. Re-check every number, hour and language before launch, and set the date.
  AGENTS.md rule 6 requires this. Reviewer and date: ______
- [ ] **H-4 (BEFORE-LAUNCH) Text lines.** Confirm each text number and keyword against its
  source: `us-crisis-text-line` (741741, `HOME`), `us-trevor-text` (678678, `START`),
  `gb-shout` (85258, `SHOUT`), `ca-kids-help-text` (686868, `CONNECT`), and the text lines with
  no keyword: `us-veterans-crisis-text` (838255), `au-lifeline-text` (0477131114) and
  `ca-988-text` (988). Reviewer and date: ______
- [ ] **H-5 (BEFORE-LAUNCH) Directory entries and the DEFAULT region.** Decide whether the
  `DEFAULT` set (112 plus two global directories) is enough for users outside IN, US, GB,
  AU and CA, or whether a region picker is needed before launch. Reviewer and date: ______
- [ ] **H-6 (BEFORE-LAUNCH) Overseas numbers.** Confirm that a person who is abroad is
  told how to reach the local emergency number (the `DEFAULT` set and the `emergency`
  instruction). Reviewer and date: ______

## D. Knowledge base (self-help articles)

Source: `backend/app/content/knowledge/*.md`, 40 files including language variants. Retrieval
serves articles in the person's language and falls back to English
(`backend/app/services/rag/retriever.py`). Authoring rules are in
[docs/knowledge-authoring-guide.md](knowledge-authoring-guide.md).

- [ ] **K-1 (BLOCKER) Clinical accuracy of every article.** Read each article and check
  the facts and the claims. Groups to check: breathing (`breathing-*`), grounding
  (`grounding-*`), exams and stress (`exams-*`, `stress-*`, `overload-*`), sleep
  (`sleep-*`), thinking traps (`traps-*`, which describe CBT ideas), worry
  (`worry-*`), activation (`activation-*`), friends and loneliness (`friend-*`,
  `loneliness-*`), self-compassion, and help-seeking (`help-*`, `trust-*`, `therapy-*`).
  Reviewer and date: ______
- [ ] **K-2 (BLOCKER) Help-seeking articles match the helpline data.**
  `help-when-to-seek.md`, `help-how-to-start.md`, `trust-telling-someone.md` and
  `therapy-what-its-like.md` must not name a helpline that is not in `helplines.json`,
  and must not promise that therapy or a helpline will help. Reviewer and date: ______
- [ ] **K-3 (BLOCKER) No outcome promises.** Search for promises of recovery or cure
  ("will fix", "guaranteed", "always works"). Reviewer and date: ______
- [ ] **K-4 (BEFORE-LAUNCH) Language variants.** Only some articles have `-hi` or `-bn`
  variants (for example `breathing-slow-outbreath-hi.md`, `exams-day-of-nerves-bn.md`,
  `loneliness-small-contacts-bn.md`, `trust-telling-someone-hi.md`). Decide whether a
  Hindi or Bengali reader gets an English article as the fallback, and whether that is
  acceptable. Native-speaker review of each variant. Reviewer and date: ______
- [ ] **K-5 (AFTER-LAUNCH) Source citations.** Check that each factual claim traces to a
  recognised source, and that the source is listed (see the authoring guide). Reviewer and date: ______

## E. Guided exercises

Source: `backend/app/content/exercises/*.yaml` (8 exercises), validated by
`scripts/validate_exercises.py`. Exercise instructions are **English-only** today
([translations/STATUS.md](../translations/STATUS.md), `exercises` row).

- [ ] **E-1 (BLOCKER) Breath-holding and breathing exercises.** `box-breathing.yaml` and
  `slow-exhale-478.yaml` include breath holds. Decide whether a warning is needed for
  people with asthma, heart or lung conditions, pregnancy, or a history of panic
  attacks or breath-holding problems. The current text says to breathe normally if
  dizzy or breathless. Decide whether that is enough. Reviewer and date: ______
- [ ] **E-2 (BLOCKER) Body-based exercises and trauma.** `body-scan.yaml` and
  `muscle-relaxation.yaml` ask the person to attend to the body. Decide whether a
  trauma-sensitive note, and a "you can open your eyes or stop at any time" line, are
  required at the start. `muscle-relaxation.yaml` already says to skip injured areas and
  stop if tensing hurts. Reviewer and date: ______
- [ ] **E-3 (BEFORE-LAUNCH) Grounding exercises.** `grounding-54321.yaml` asks the person
  to notice sensations and memories. Confirm the "leave it out or stop" wording is enough
  for someone who dissociates or has a trauma memory triggered. Reviewer and date: ______
- [ ] **E-4 (BEFORE-LAUNCH) Worry and planning.** `worry-time.yaml` and
  `small-action-planner.yaml`. Confirm the instructions do not encourage rumination,
  and that the stop rule ("if this becomes repetitive rumination... stop") is clear. Reviewer and date: ______
- [ ] **E-5 (BEFORE-LAUNCH) Emotion labelling.** `emotion-labelling.yaml`. Confirm the
  instructions are accurate and that the "the feeling does not have to disappear"
  message is appropriate. Reviewer and date: ______
- [ ] **E-6 (BEFORE-LAUNCH) Exercises in Hindi and Bengali.** Decide whether to translate
  the eight exercises before launch in those locales. Until then they are English only, and
  the app says so. Translation review: ______

## F. Screening presentation (PHQ-9 and GAD-7)

Source: `backend/app/content/assessments/phq9.json` and `gad7.json` (item wording,
scoring, `safety`, `cadence`, `disclaimers`, `provenance`), the result page, and the
[model card §6](model-card.md#6-screening-instruments-day-20).

- [ ] **S-1 (BLOCKER) Item wording.** Re-check the English item wording character by
  character against the official sheet (the model card says this was done on 2026-10-11).
  Confirm the non-affiliation and licence statements are still accurate. Reviewer and date: ______
- [ ] **S-2 (BLOCKER) Score bands and band copy.** The bands (PHQ-9: 0–4, 5–9, 10–14,
  15–19, 20–27; GAD-7: 0–4, 5–9, 10–14, 15–21) come from the instruments. Every sentence
  Manovia writes about a band is the project's and has **not** been reviewed by a
  clinician. Review each band's copy. Reviewer and date: ______
- [ ] **S-3 (BLOCKER) Item-9 presentation.** `phq9.json` `safety.explanation` and
  `encouragement`. Confirm the support is shown before the score, that the helplines are
  reachable from that screen, and that the wording does not frighten or minimise. Reviewer and date: ______
- [ ] **S-4 (BLOCKER) "Not a diagnosis" language.** The phrase "screening result, not a
  diagnosis" appears on the result. Check that it cannot be read as reassurance that
  nothing is wrong, and that nothing implies a diagnosis. Reviewer and date: ______
- [ ] **S-5 (BEFORE-LAUNCH) Cadence.** `cadence.recommended_interval_days` is 14. Confirm
  the interval and the "trend" wording do not encourage compulsive re-screening. Reviewer and date: ______
- [ ] **S-6 (BEFORE-LAUNCH) Next steps.** Check what the result page says for scores in the
  moderate and severe bands. Does it point to a clinician or a helpline, and does it say what
  the person can do next? Confirm the copy in the `bands` block of each instrument. Reviewer and date: ______
- [ ] **S-7 (BEFORE-LAUNCH) Instrument language.** The instrument items are English-only
  (`translations/STATUS.md`, Clinical instruments). Decide whether to commission validated
  translations before launch in hi and bn, or to keep English and say so. Do not
  machine-translate the instrument items. Reviewer and date: ______

## G. Launch governance

- [ ] **G-1 (BLOCKER) Named reviewers.** Name one clinical reviewer for sections A to F,
  and record the name, licence type and date for each. Reviewer(s): ______
- [ ] **G-2 (BLOCKER) Native-speaker reviewers.** Name a Hindi reviewer and a Bengali
  reviewer, and record the review in `translations/STATUS.md` with their names and dates
  (the Day-24 translation pipeline). Reviewers: ______
- [ ] **G-3 (BLOCKER) Safety and conduct reporting address.** Replace the owner-only
  contact route in `CODE_OF_CONDUCT.md` and `SECURITY.md` with a monitored address or a
  GitHub private vulnerability reporting route. At the time of writing, private
  vulnerability reporting is **disabled** on the repository. Owner: ______
- [ ] **G-4 (BLOCKER) Privacy and consent text.** `backend/app/content/consent_documents.json`
  and the privacy settings text are reviewed by someone competent in privacy law for the
  launch regions. Reviewer and date: ______
- [ ] **G-5 (BLOCKER) Crisis disclaimer placement.** The "not a substitute for care, call
  your local emergency number" notice is on the landing screen, in the chat, and in the
  README. Confirm placement and wording. Reviewer and date: ______
- [ ] **G-6 (BEFORE-LAUNCH) Incident procedure.** [docs/runbook.md](runbook.md) has a
  safety-incident procedure. A clinician reviews it, and names who is on call. Reviewer and date: ______
- [ ] **G-7 (BEFORE-LAUNCH) Monitoring after release.** Decide which numbers are reviewed
  and how often: `safety_events_total` by level, the share of crisis cards per week, and
  complaints about false alarms. Name the owner. Owner: ______
- [ ] **G-8 (AFTER-LAUNCH) Re-review cadence.** Set a date for the next full review of
  sections A to F, and for re-checking the helpline data (H-3). Next review: ______

---

## Sign-off

No one has signed this checklist. Do not record a sign-off you have not given.

| Section | Reviewer (name, credential) | Date | Decision summary |
| --- | --- | --- | --- |
| A. Crisis messages | | | |
| B. Escalation policy | | | |
| C. Helpline data | | | |
| D. Knowledge base | | | |
| E. Guided exercises | | | |
| F. Screening presentation | | | |
| G. Launch governance | | | |
