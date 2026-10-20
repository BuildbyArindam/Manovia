# 5-minute technical walkthrough

A script for a screen-share or a whiteboard. Each block has **what to show**, the
**one sentence that matters**, and the **question it invites** (so you are not
surprised by it).

---

## 0:00 — Frame it (30 s)

**Show:** the README's first screen and the AI/ML table.

> "One product, one guarantee: nothing that can generate text runs before the
> crisis check. Everything else in this repository is downstream of that."

**Invites:** "Isn't that just a keyword filter?" → go to 0:40.

## 0:30 — The request path (60 s)

**Show:** `docs/assets/v2-architecture-poster.svg`, middle column.

> "Auth and consent, then crisis assessment — normalisation, rules, and an ML
> backstop that may only raise. High risk returns a pre-written card and stops:
> no model call, no streamed tokens. Otherwise: redaction, retrieval, the provider
> chain, and a whole-reply output guard before anything is stored."

**Say the ordering out loud:** the ML model cannot lower a level, cannot write the
card, never sees an identifier. That is enforced by types and by tests, not by
convention.

**Invites:** "What if the rules miss something?" → "Then the backstop may raise
the level — one direction only — and the evaluator scores rules-only recall
separately (0.333 on dev) so a rule regression cannot hide behind the model."

## 1:30 — The safety numbers, including the ugly one (45 s)

**Show:** `evals/reports/2026-10-20/safety.md`.

> "Recall 1.000 with a 95 % interval of 1.000–1.000 on 51 crisis cases; precision
> 0.543. That second number is the price: about a third of benign messages get the
> crisis card. Here is a live example from the journey run — 'I had a long day and
> I am tired' escalated with ml.crisis_mass 0.372."

**Why this wins interviews:** you volunteered the weakness before they found it.

## 2:15 — Reproducibility and gates (60 s)

**Show:** `ml/dvc.yaml` (3 lines), `ml/configs/release_gates.yaml` (fairness block),
then `ml/tests/test_fairness_audit.py`.

> "Every run logs commit, data hashes, config, seed and library versions to
> MLflow. Gates fail the build: fairness across eleven identity dimensions,
> model-size and INT8 ratios, DP budget, and reproducibility against a committed
> baseline.

> Two of these were broken until the release verification and both were invisible
> on the day they were written: the fairness report was seeded from Python's
> salted `hash()`, so it changed between identical runs; and
> `max_gap_code_mixing` never matched the `code-mixing` category, so that
> dimension silently ran against the loosest default. A test now runs the gate in
> two processes with different PYTHONHASHSEED values and compares bytes."

**Invites:** "How did you find them?" → "A fresh clone and a second run. Which is
why the release verification is a fresh clone, not a re-run in place."

## 3:15 — Private Mode (60 s)

**Show:** `frontend/src/features/private/`, then the parity output.

> "Same rules, compiled to JavaScript from the same backend content, loaded as a
> lazy chunk and not precached. 500 out of 500 phrases agree with Python. With the
> network blocked, a conversation makes zero requests. Messages are ciphertext in
> IndexedDB under a passphrase-derived key. The model runtime is an injectable
> loader, so people who never enable it download none of it — and because no model
> is pinned in this repository, replies are templates and the badge says so."

**Invites:** "Isn't a second implementation a liability?" → "It is, which is why
there is one source of truth (the backend content), a `--check` on every exporter,
and a 500-phrase parity gate that fails if either side moves."

## 4:15 — The release evidence (45 s)

**Show:** `docs/verification/day49-verification.md` PASS/FAIL table and
`scripts/verify_day49_journey.py` output.

> "5,524 backend and 2,917 frontend tests, coverage 94.28 % / 85.50 %, lint and
> types clean, bundle 133.1 KB of a 135 KB budget, and a 13-step journey against a
> real database: onboarding, chat, crisis, mood, outlook opt-in, exercise, journal,
> agent, clinician share with revoke, export, delete. I say plainly which checks a
> browser would have run and which I could not run here."

## 5:00 — Close on the limits (15 s)

> "What is missing is not code: clinical review, native-speaker review of Hindi
> and Bengali, an ethics and security review by other people, and user research.
> The next engineering step is recalibrating precision against consented real
> text."

---

## Whiteboard, if they ask for it

Draw three columns — device, server, `ml/` — and **one arrow direction** from
`ml/` into the server labelled *gated release*. The line to say: "a number becomes
a serving change only through a committed report, a card and a green gate; there
is no path from a notebook to a user."
