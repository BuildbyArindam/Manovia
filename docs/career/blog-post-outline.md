# Blog post outline

**Title:** *The neural model lost to a moving average, and I shipped the moving
average*

**Subtitle:** Three months building a safety-first mental-wellbeing app, and the
five numbers I had to publish against my own interests.

**Audience:** engineers who build ML-adjacent product features; people who have
been told to "add AI" to something that should not have AI in the first place.

**Length:** 2,000–2,500 words. Every number links to a file in the public repo.
Nothing in the post is a claim the repository cannot back.

---

## 1. Hook (200 words)

- Open with the 3 a.m. message: *"I have decided to end my life tonight. I have a
  plan."* — and the fact that the correct product response is to **not** let a
  language model answer it.
- The tension in one line: *a generative model is the wrong tool for the most
  important moment, and a useful tool for every other moment.*
- Promise: five measurements, including the ones that made the product worse-
  sounding and better.

## 2. The ordering rule (350 words)

- Rules before model; a backstop that may only raise caution; a whole-reply output
  guard; no identifiers in prompts; no raw text in logs.
- Why "raise-only" is the interesting part: it makes the model an *advisor* to a
  deterministic system instead of an authority.
- Show the code shape (`pipeline.py`, the ensemble's return type), not a diagram.
- **The test that keeps it honest:** rules-only recall is a separate CI gate
  (0.333 on dev), so a model gain cannot mask rule rot.

## 3. Measurement 1: recall 1.000, precision 0.543 (300 words)

- The split: 116 synthetic cases, 51 high/imminent, four language strata.
- Bootstrap intervals, with the honest sentence: 51 crisis cases do not support
  two decimal places of confidence.
- The cost, with a real example: *"I had a long day and I am tired"* escalated to
  **high** (`ml.crisis_mass` 0.372).
- Why recall-first is still the right call *and* why that example belongs in the
  README, not in a footnote.
- **What real clinical data would change:** the threshold, the taxonomy, and the
  belief that one number can describe a population.

## 4. Measurement 2: the transformer that lost (350 words)

- The setup: 72 synthetic users, 210 days, disjoint user groups, forward-only
  windows, split-conformal intervals.
- The table: moving average 0.554 MAE, transformer 0.561, GRU 0.584, LSTM 0.621.
- What a disciplined team does next: ship the baseline, keep the experiment, write
  the negative result where the next engineer will find it.
- The product consequence: Outlook is opt-in, numeric-only, withholds under 14
  distinct days, and says "not a calibrated interval".
- **The uncomfortable paragraph:** a demo with the neural number would have looked
  better and helped less.

## 5. Measurement 3: the gate that was silently permissive (250 words)

- `max_gap_code_mixing` vs category `code-mixing` — a string mismatch that made a
  0.12 ceiling behave like a 0.15 default.
- And the reproducibility bug next door: `hash()` is salted per process, so the
  fairness report changed between identical runs.
- Lesson: **a gate is code, and code has the same review needs as the model.**
- The test that now prevents it: run the gate in two processes with different
  `PYTHONHASHSEED` and compare bytes.

## 6. Measurement 4: parity, or how to have two safety engines (300 words)

- Compile the content, never duplicate it; `--check` on every exporter.
- 500/500 phrases identical; 56 normalisation disagreements pinned as data.
- The failure mode you choose: templates when no model is pinned, an honest badge,
  and no "just this once" network call.
- Why the second implementation is worth the maintenance: it is the only version
  where "nothing leaves the device" is checkable.

## 7. Measurement 5: budgets as product decisions (250 words)

- 133.1 KB gzip of 135 KB initial JS; 744.5 KB precache of 800 KB; heavy modules as
  their own chunks.
- The rule: **a page is a route, not an import.**
- Why zero third-party requests is a safety budget, not a performance one.

## 8. What I would not ship again / what I would do first (200 words)

- Not ship: a bandit on someone's first week; a federated claim without clients.
- Do first: clinical review, native-speaker review, recalibration against
  consented real text, one honest user study.

## 9. Close (150 words)

- "The most useful artefact in the repository is the limitations section."
- Invite the reader to run `make eval` and disagree with a number.

---

## Publishing checklist

- Every number links to a committed report or command (no screenshots of numbers).
- The post names what is synthetic. Twice.
- No clinical claim, no "validated", no "safe" without a scope.
- Add the one-line crisis note at the top: *if you are in danger, call your local
  emergency number (112 / 911 / 999 / 000).*
- Cross-link the [technical report](../technical-report.md), not the README.
