# The 2-minute pitch

Say it out loud once before you use it. Times are approximate; the sentences in
**bold** are the ones to keep if you are cut off at 60 seconds.

## 0:00–0:20 — The problem, concretely

"People reach for their phone before they reach for a professional — at 3 a.m.,
when nobody is answering. Most apps meet that moment with a chatbot that will
happily improvise when someone says they want to die. I built Manovia to see
whether you can make that moment safer *and* still useful."

## 0:20–0:55 — The one design decision everything follows from

"**Manovia's safety path is older than its AI.** Crisis detection is
deterministic: normalisation, rules, then a calibrated ML backstop that is only
allowed to *raise* caution and never to lower it. If a message is high risk, the
model is never called — the person gets a pre-written card in their language,
with helplines and an emergency number. Every reply that does come from a model
passes a deterministic output guard before it is stored or shown.

That ordering is why the rest of the system exists: because the model is not in
charge, I can let it fail, degrade, or be turned off."

## 0:55–1:25 — What v2 added, with numbers

"Version 2 added an ML platform and three research tracks. The platform has DVC
data, MLflow runs and release gates — fairness across eleven identity dimensions,
model size, quantisation, reproducibility — that fail the build. The research was
honest: the mood-forecast transformer **lost to a seven-day moving average**
(MAE 0.561 vs 0.554), so I ship the moving average and say so in the UI. The
bandit and the federated learning are simulated, measured, and not shipped.

The piece I am proudest of is **Private Mode**: the same safety engine compiled to
JavaScript, running in the browser with **no network at all** — 500 out of 500
phrases produce the identical risk level as the Python engine."

## 1:25–1:50 — The hard trade

"The cost of recall-first is false alarms: precision is 0.543, so about a third of
ordinary messages get the crisis card. In the live journey, *'I had a long day and
I am tired'* escalated. I could hide that; instead it is in the README's
limitations section, because a safety system whose failure modes are secret is not
a safety system."

## 1:50–2:00 — Close

"**5,500 backend tests, 2,900 frontend tests, 13-step end-to-end journey, all
green on the tagged commit** — and a limitations section that says what it is not:
not clinically reviewed, no native-speaker sign-off, no real user data. What I
would do next is the boring, valuable part: clinical review and recalibration
against consented real text."

---

## If they ask "so what did you actually build?"

Pick two: the **ordering guarantee** (rules before model, raise-only backstop,
output guard) or the **on-device parity** (one safety contract, two runtimes, a
500-phrase gate). Both are design decisions with tests, not features with
screenshots.
