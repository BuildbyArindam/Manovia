# Elevator pitch — 2 minutes (~280 words)

> Spoken, conversational. Timed at a normal pace this runs just under two
> minutes. Numbers are the repository's own (see resume-bullets.md for the
> file mapping).

---

I spent thirty days building Manovia — a mental-wellbeing self-help
companion — and the interesting part isn't the chat feature, it's the order
of operations. In Manovia, the language model is the *least* trusted
component in the system.

Here's what I mean. Every message first passes a deterministic, reviewable
rules engine — plus a small ML backstop that can only ever *raise* the risk
level, never lower it. If someone is in crisis, the LLM is never called at
all: they get a pre-written, clinically-cautious response with real
helplines, in their language. And everything a model does say has to pass a
deterministic output guard before a user sees it. Rules first, model second.

I treated safety as an engineering discipline, not a disclaimer. There's an
offline evaluation harness with bootstrap confidence intervals wired into
CI — on the crisis dev split the ensemble catches every high-risk case, and
the gate fails if a single pattern regresses. Then I red-teamed my own
system: a 150-case adversarial suite — euphemisms in six languages, prompt
injection, jailbreaks, access-control probes. The first run scored 82 out of
140. I fixed every finding with a regression test — including a truncation
bug that made long messages invisible to risk checks — and the final run is
150 out of 150.

It's also privacy-first: field-level encryption, PII redaction before any
model call, full export and delete.

And I'm careful about what I don't claim: it's a beta portfolio demo, not a
clinical service — the datasets are synthetic, and clinical review is the
explicit gate before it could ever be more. That honesty is part of the
design.

---

**One-breath version (15 seconds):** "I built a mental-health companion where
the LLM is the least-trusted component — deterministic crisis rules run
before any model call, an eval harness gates CI, and my own 150-case red-team
suite passes 150 out of 150. And I'm precise about what it is: a portfolio
demo, not a clinical service."
