# LinkedIn post draft

> Plain text, no hashtag spam, honest framing. Attach the social preview
> image (`docs/assets/social-preview.png`) or a short screen recording.

---

For the last 30 days I built Manovia in public: a privacy-first,
safety-first mental-wellbeing companion. Today I tagged v1.0.0.

One design decision shaped everything: **the language model is the
least-trusted component in the system.**

→ Crisis detection is deterministic and runs *before* any LLM call. A
high-risk message never reaches the model — the person gets a pre-written
response with real helplines, in English, Hindi or Bengali.
→ An ML backstop can raise the detected risk level, but can never lower it.
→ Every model reply passes a deterministic output guard before anyone
sees it.

And because "trust me" isn't engineering:

→ An offline evaluation harness with bootstrap confidence intervals runs in
CI. On the 116-case crisis dev split the ensemble catches all 51 high-risk
cases; weakening one detection pattern fails the build.
→ I red-teamed my own system with 150 scripted adversarial cases —
euphemisms across six languages, prompt injection, jailbreaks, IDOR. First
score: 82/140. After fixing every finding (each with a regression test):
150/150.
→ Privacy is structural: field-level encryption, PII redaction before any
model call, full export and delete, no raw user text in logs.

What it is NOT — and says so in its own footer: a clinical service. It's a
beta portfolio demo. The datasets are synthetic, the Hindi and Bengali still
need native-speaker review, and a licensed clinician reviewing the crisis
content is the explicit gate before this could responsibly face real users.
Shipping that sentence felt as important as shipping the code.

Repo (MIT, with the full evaluation reports and the red-team report):
https://github.com/BuildbyArindam/Manovia

I'd genuinely value scrutiny — especially from clinicians, safety
researchers, and native Hindi/Bengali speakers. Issues and Discussions
are open.

#buildinpublic #aisafety #mentalhealth #opensource
