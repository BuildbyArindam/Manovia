# 30 deep-dive interview questions, with answers that hold up

The answers are written to be *usable*: start with the position, then the
evidence, then the limit. Each links to the artefact you can open on a screen
share. Where the honest answer is "we did not measure that", it says so — that is
the point of this file.

Grouped by theme, but expect them in any order.

---

## A. Why rules first (Q1–Q4)

### 1. Why not let the LLM make the crisis decision? It's a better language model than your rules.

"Because the failure is unbounded and one-directional. If a rule misses, the ML
backstop can still raise the level; if a model misses, nothing downstream can
recover the moment — the person gets a fluent reply instead of a card. My rules
are inspectable, deterministic and identical on device and server; a model's
boundary can move with a provider upgrade I do not control. The ordering also
means I can *turn the model off* — there is a kill switch — and the safety
guarantee survives. That is not true in the other design."

Evidence: `backend/app/services/safety/pipeline.py`,
[ADR 0008](../adr/0008-crisis-detection-rules-engine.md).

### 2. Rules have terrible recall — yours is 0.333 alone. Isn't that a failure?

"It is a floor, not the product. The shipped path is rules plus a calibrated
backstop at recall 1.000 on the dev split. I keep the rules-only number because it
is the regression detector: if a pattern is commented out or weakened, that number
moves and CI fails. A high-recall model can mask rule rot indefinitely — that is
a specific failure mode I designed a gate against."

Evidence: `evals/reports/2026-10-20/safety.md`, `evals/thresholds.yaml`.

### 3. How do you know the ordering is actually enforced, and not just documented?

"Three ways: the orchestrator type cannot return a model reply at high risk
(the policy object carries `allow_llm: false`), the evaluator asserts the crisis
card is present and the LLM was not called, and the red-team suite includes
prompt-injection attempts whose expected outcome is the deterministic reply. The
journey verifier then does the same assertion over HTTP against a real database."

Evidence: `scripts/verify_day49_journey.py` step 4,
`backend/tests/redteam/`.

### 4. What happens when the rules and the model disagree?

"The model may raise, never lower. If rules say LOW and the model says HIGH, the
ensemble takes HIGH and records `ml.crisis_mass` in the rationale. If rules say
HIGH and the model says NONE, the level stays HIGH and the model's contribution is
visible in the audit fields. There is no code path where a lower model level
overrides a rule."

---

## B. Calibration (Q5–Q9)

### 5. Your model is calibrated — what does that actually mean here?

"Two different things, and I try not to blur them. The *level* classifier uses
temperature scaling fitted on a held-out calibration split, so its probabilities
are closer to frequencies than a raw softmax. Separately, the ensemble has
operating thresholds (`min_confidence`, `crisis_mass_floor`) tuned to hit a recall
target, which deliberately moves the decision boundary away from 0.5. So:
calibrated probabilities, uncalibrated *operating point* — the second is a product
decision, not a statistical one."

### 6. How would you detect that the calibration has drifted?

"Two alarms. Offline, a fresh labelled split re-runs calibration and compares the
quantile to the committed value; the reproducibility gate fails if it moves.
Online, the drift monitor tracks abstention rate, confidence rates and safety-level
mix on privacy-only aggregates, and fires PSI (>0.2) or KS (p<0.05) alerts — never
text, never per-user rows. What it cannot do is tell you the labels changed;
that needs a re-labelled sample."

Evidence: `backend/app/services/drift/monitor.py`,
[day48-verification](../verification/day48-verification.md).

### 7. Reliability diagrams, ECE — where are they?

"Not in the committed reports for the safety level classifier, and I would not
claim them. The Day-33 pipeline writes temperature and the conformal quantile; the
smoke report prints coverage and set size. An ECE curve on a 20-row synthetic
smoke split would be noise. The right framing: the machinery is there, the data
to draw a meaningful reliability diagram is not."

### 8. Your precision is 0.543. Would a business ship that?

"Not unchanged. It is the consequence of a recall-first threshold chosen for a
product where a false negative is a person in danger and a false positive is an
unhelpful card. In a clinical deployment I would want a second operating point —
different thresholds by risk category, a 'check in with me' intermediate state,
and a human review of everything the backstop raises. The point of publishing
0.543 is that the trade is visible."

### 9. If you had labelled real data tomorrow, what would you change first?

"The threshold and the taxonomy, in that order. Real crisis language is more
indirect, more code-mixed and more contextual than my hand-written cases; I would
expect my recall to fall and my precision to rise at the current threshold, which
means the current operating point is tuned to a corpus, not to people. I would
also re-cut the categories: 'plan' and 'means' are not the same signal as
'hopelessness'."

---

## C. Conformal abstention (Q10–Q13)

### 10. Explain your conformal prediction setup in one minute.

"Split conformal on a held-out calibration split: for each example I compute the
nonconformity score of the true label (1 − p_true) and take the ⌈(n+1)(1−α)⌉
quantile. At inference the prediction set is every label whose score is under the
quantile. With α = 0.05 that gives a 95 % coverage target. The wrapper then
abstains in a specific way: before the model can *lower* a risk level, it must be
sure the set agrees with the rule's level; otherwise the level stays and the turn
is flagged. Coverage is measured on held-out rows (0.95 target, 0.95 observed in
the smoke run) and mean set size is 4.85 of 5 levels — which honestly means the
smoke model is barely informative."

Evidence: `ml/src/manovia_ml/training/safety/` (conformal + abstention policy),
`ml/reports/day34-safety-compression.md`.

### 11. Conformal guarantees are marginal, not conditional. Does that matter for safety?

"Very much, and it is the reason abstention is a *safety* mechanism rather than a
convenience. Marginal coverage can hide terrible conditional coverage — for a rare
language or an unusual phrasing the set can be smaller than advertised at exactly
the moment you need it. Mitigations: per-language evaluation splits (en, hi,
hi-Latn, bn), reporting coverage per stratum rather than pooled, and treating any
stratum with a small set as untrustworthy. That is also why the abstention policy
is 'abstain to the rule's level', not 'trust the set'."

### 12. Why does the model need to abstain at all if rules decide?

"Because raising a level has a cost. If the model raises every medium message to
high, the crisis card becomes wallpaper. Abstention is the mechanism that lets the
model say *'I am not sure enough to escalate this'* while still allowing it to
escalate when it is. Concretely: raise only when the prediction set excludes the
lower level with the calibrated quantile."

### 13. What breaks conformal prediction here?

"Exchangeability. Calibration rows are drawn from the same distribution as test
rows in my splits — by construction, since I generated both. Real traffic is not
exchangeable: new slang, new scripts, seasonal effects, and a population that
changes after every news event. The practical answer is adaptive or weighted
conformal with a recency-weighted calibration set, and a monitoring alarm when
coverage on a labelled shadow set drops."

---

## D. Federated versus centralised (Q14–Q17)

### 14. Why would anyone do federated learning for a mood model?

"The premise is that check-in data is the kind that should not be centralised at
all, even encrypted, because the risk lives in the aggregate. Federated keeps raw
rows on device and sends model updates. My simulation reaches centralised quality
within 0.06 MASE (DP σ=3.2, C=1.0: 1.2754 vs 1.3372) at ε = 0.8417, δ = 1e-5."

### 15. Then why didn't you ship it?

"Three reasons. There are no clients — a simulation is not a deployment, and the
security of aggregation depends on a threat model I have not had reviewed. The
privacy budget is a claim about a simulator with synthetic users; ε is only
meaningful relative to a mechanism, a sampling scheme and an adversary. And
operationally, federated learning needs a coordinator, secure aggregation and a
story for devices that never come back. Shipping it would have been a slide, not a
feature."

### 16. What are the actual privacy attacks you would test?

"Membership inference, attribute inference, and gradient/update inversion. My
simulation reports an attack AUC per configuration (≈0.55, i.e. close to chance)
as a weak sanity check — not a proof. A real evaluation needs a stronger adversary
with auxiliary data, plus a canary-based DP audit to check the reported ε against
an empirical estimate."

### 17. Centralised with DP instead — why not? It's simpler.

"It is simpler, and for this product it is probably the right first step. The
reason federated stays interesting is the trust story: 'we never received your
rows' is a stronger statement than 'we added noise to the aggregate'. If I were
shipping tomorrow I would start centralised with DP-SGD at a small budget and a
documented per-user accounting, and treat federated as a later migration with the
same evaluation harness."

---

## E. Bandit safety constraints (Q18–Q21)

### 18. A contextual bandit is the obvious way to personalise interventions. What's the catch?

"The catch is that exploration on a wellbeing product means deliberately offering
a worse option to a person who is having a bad week, to learn something. My
simulator shows the payoff: LinUCB cumulative reward 193.795 [186.693, 200.897] vs
random −73.388, and a regret curve that flattens early. It also shows the cost:
it needs about 60 decisions per user before it looks good, which is longer than
most people stay."

### 19. What safety constraints did you put on the policy?

"Three. A hard 20 % cap on exploratory suggestions per user. Never two exploratory
suggestions in a row. And a wrapper that sits *outside* the policy: safety levels
and crisis state bypass the bandit entirely — the bandit only chooses among
already-appropriate, curated options. The reward is a bounded pre/post rating
change, with a penalty only for a started-and-abandoned session; a skip is missing
data, not a failure."

Evidence: `ml/reports/day37-jitai.md`, `ml/configs/jitai.yaml`.

### 20. How would you evaluate a policy without an A/B test?

"Off-policy evaluation with IPS and doubly robust estimators against logged
uniform behaviour, on a disjoint training portion. In the simulation, IPS estimates
0.3917 [0.3741, 0.4094] against a ground truth of 0.3854 — an absolute gap of
0.0063. The honest caveat is that OPE is one-step, assumes overlap, and real logs
are confounded by the very interventions you are evaluating."

### 21. Reward hacking — what would go wrong with real users?

"People learn the interaction. If a rating change is the reward, the system will
converge on the option that most reliably produces a self-reported improvement —
which may be the one that asks for a rating at the best moment, not the one that
helps. Mitigations: pre-register the reward, keep the reward signal sparse and
delayed, monitor for 'rating-seeking' copy, and never let the bandit change the
words of the safety path."

---

## F. Privacy attacks and defence (Q22–Q25)

### 22. Your share link has the key in the URL fragment. Why is that safe?

"Fragments are not sent to the server, so the server never sees the key — it only
sees a token. The key is derived (HKDF-SHA256) from the fragment secret, the
passcode and the token, so neither the database nor a log contains the material
that decrypts the document. The costs I accept: anyone with the full URL can open
it until it expires (mitigated by TTL, view budget, passcode and revocation), and
browser history on a shared device is a real risk — which is why the page says so."

Evidence: [ADR 0047](../adr/0047-clinician-share-bridge.md),
`docs/clinical-sharing.md`.

### 23. What about the ciphertext you store — could someone with DB access forge a document?

"AAD binds the ciphertext to the token and the document name, so a swapped or
truncated document fails authentication. What an attacker with database access can
still do is deny service (delete rows) or replay an older, valid ciphertext for
the same token — I do not store a version chain, so that is a real gap and it is
written down."

### 24. What's your threat model for the LLM provider, and where does it leak?

"The provider sees redacted text plus a system prompt, and no identifiers. The
leaks I worry about: (a) redaction misses — mitigation is a conservative redactor
plus tests that inject identifying strings and assert they do not appear in the
outgoing payload; (b) the provider's own retention — a contractual question, and
the reason Private Mode exists; (c) prompt injection turning the assistant into a
data exfiltrator — mitigated by the output guard and by the absence of tools in
the chat path."

### 25. How do you know no message text reaches your logs?

"By construction and by test: the structured logger drops the raw-text keys
(`message_text`, `content`, `body`, `note`, credentials) before writing, and an
end-to-end test asserts that a chat turn produces no log line containing the
text. The uvicorn access log is disabled in the dev target for the same reason —
it logs the full request line, including query strings."

---

## G. Evaluation limits (Q26–Q28)

### 26. You scored 51 crisis cases. What's the real uncertainty?

"Recall 1.000 with a 95 % bootstrap interval of [1.000, 1.000] is a statement
about *this* sample, not about the world. With 51 positives, the exact one-sided
95 % lower bound is 0.930 — that is the number to quote, and even it assumes the
cases are representative. They were written by one person. The honest summary is:
'we found no false negatives among 51 synthetic cases'; anything stronger is
overclaiming."

### 27. Why not just use a public benchmark?

"Because the interesting failure modes are language- and product-specific. A
benchmark would not tell me whether a Bengali somatic description ('বুকটা ধড়াস
করে উঠল') is caught by my rules, and my catalogue marks several public mental-
health corpora as restricted or DO NOT USE for licence or personal-data reasons.
The trade I made: synthetic and inspectable, with the limitation stated in every
report."

### 28. Your dev split has been looked at. Isn't that contamination?

"Dev has, and I say so: the numbers in the README are dev numbers, not held-out
results. The crisis test split is frozen by SHA-256 in a manifest and has been
scored at least once, which is why the evaluation report calls it 'not a clean
held-out result'. The stronger claim I can make is about *procedure*: neither the
trainer nor the threshold tuner reads the test file, and a test asserts that."

---

## H. What would change with real clinical data (Q29–Q30)

### 29. Walk me through the first month if a clinic gave you de-identified logs.

"Week 1 is not modelling: it is governance — a data-use agreement, an ethics
review, a defined retention period and a hashing scheme for patient ids. Week 2 I
would re-label a stratified sample (language, script, code-mixing, risk level)
with two clinical raters and measure inter-rater agreement; without that, every
downstream metric is measuring my own taxonomy. Week 3, recalibrate the level
classifier and the operating thresholds on real text, and report per-stratum
coverage. Week 4, shadow-mode the whole pipeline against the clinic's existing
triage and count disagreements — including the false positives, which is the
number clinicians actually care about."

### 30. What would you expect to break first with real data?

"Three things, in order. **Coverage**: real crisis language is more indirect and
more code-mixed than my cases, so recall will drop at the current threshold.
**Precision**: the escalation rate will look worse before it looks better, because
real people write about distress elliptically. **Trust in the UI**: the crisis
card will appear for messages that are clearly not crises, and a clinician will
ask me why. That question is the beginning of the real product work — and it is
the reason the limitations section is the longest part of my technical report."

---

## How to use this file in an interview

- Do not recite. Pick the two or three questions that map to the role and answer
  those with the artefact open.
- Every answer above ends in a limit. If an answer of yours does not, you are
  probably overclaiming.
- If you do not know, say "not measured" and describe the experiment you would run.
  That is the behaviour this project is a portfolio of.
