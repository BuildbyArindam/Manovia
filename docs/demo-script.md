# Demo script: 90 seconds

A walkthrough for a portfolio reader, a funder, or a reviewer who has never run
Manovia. It shows what the product does and where its safety lines are. It
does not claim the product is clinically validated, launched, or complete.

**Status:** this script is written. The recording has **not** been made, and the
timings below have not been measured on video. Treat them as the target.

## Before you start

- Run the stack (`make up`, then open http://localhost:3000), or use the
  production build described in [docs/deploy.md](deploy.md).
- Use a fresh guest account and synthetic text only. Never use real personal
  messages in a recording.
- For the crisis moment, use one of the scripted demo lines. Do not improvise a
  risk statement on camera. Do not read out method details, and do not show the
  evaluation datasets.
- Have a second window open on the [model card](model-card.md) so you can point
  at the limits.

## Shot list

| Time | On screen | Narration (plain, not salesy) |
| --- | --- | --- |
| 0:00–0:10 | Home screen, with the notice at the top | "Manovia is a self-help companion. It is not therapy, not a diagnosis, and not a crisis service." |
| 0:10–0:25 | Tap a face on the quick check-in and save | "A ten-second check-in. Notes are encrypted, and the user can export or delete everything." |
| 0:25–0:40 | Chat: send an ordinary, scripted question and show the reply | "Ordinary messages get a grounded reply: redacted before any model sees them, checked by a deterministic output guard before they are shown." |
| 0:40–0:55 | Chat: send the scripted high-risk line; the crisis card appears | "High-risk messages never reach a language model. The reply is pre-written, in the user's language, with helplines and the emergency instruction." |
| 0:55–1:10 | Turn off the network. Open **Need help now**; then start a breathing exercise | "The helplines saved on this device still show offline, and the breathing exercise works with no signal." |
| 1:10–1:20 | Settings: high-contrast palette; then the Accessibility page | "Built to aim for WCAG 2.2 AA, with an accessibility statement that says what is and is not done." |
| 1:20–1:30 | Open [docs/model-card.md](model-card.md) and scroll to the failure modes | "Here is what the models can't do, the open clinical review, and the limits. Those are part of the product, not a footnote." |

## Lines to avoid

Do not say any of the following, because the repository does not support them:

- "It detects every crisis." The ensemble's recall on the test split is high, but the
  split is small (51 high-risk and imminent cases), and the test set has been
  scored before. Use the evaluation report for the precise figures.
- "Clinically validated," "approved," or "reviewed by clinicians." The checklist
  in [docs/clinical-review.md](clinical-review.md) has no sign-offs.
- "All helplines are verified." Some entries in
  [helplines.json](../backend/app/content/helplines.json) are flagged
  `needs_verification`, including KIRAN. Say the list is checked before use and
  that the numbers should be confirmed locally.
- "Live," "launched," or "in production." The Fly.io and Netlify deployment is
  planned in [docs/deploy.md](deploy.md) but has not been run with real accounts.

## After the recording

- Add the recording and its date to [docs/verification/day28-manual-tests.md](verification/day28-manual-tests.md).
- If any scripted line, helpline, or screen changes, update this script in the same commit.
