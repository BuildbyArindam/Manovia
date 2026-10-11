# ADR 0019 — Seven-step CBT thought record

Date: 2026-10-11 · Status: accepted

## Decision

Offer an optional seven-step self-help reflection: situation, automatic thought,
emotion and initial intensity (0–100), evidence for, evidence against, the
person's own alternative thought, and re-rated intensity. The browser screens
written steps as they advance; the API **screens all five written fields again**
on create, so skipping the wizard cannot bypass safety. At HIGH or IMMINENT,
stop before any LLM call or write and return the pre-written regional crisis card
and helplines. Detector failure fails closed (503). "Continue later" holds the
unsaved draft in current-tab memory only; reloading loses it. Resume never
bypasses screening. No partial HIGH-risk record is saved.

Use the Day-17 per-user AES-GCM envelope for each free-text column, with distinct
`cbt.<field>` AAD and the owner's wrapped DEK; ratings and closed-set emotion /
what-helped labels are the only clear values. Ownership is checked on every
read/delete. Migration `0006` adds the table and the key rotation path includes
these columns so retiring an old master key does not strand them.

The optional trap helper first runs the same safety ensemble, then redacts
identifiers before calling the existing LLM chain. The full reply passes the
Day-14 output guard. Only exact IDs in the ten-element taxonomy are parsed from
a JSON array; **no model-authored prose** is displayed. Plain-language
explanations and a question (not a balanced thought) come from static local
copy. Guard rejection, malformed JSON, canned provider reply, or provider
failure falls back to deterministic keyword matches (max three). This is a
reflection prompt, never a diagnosis or a claim that a thought is wrong.

## Trade-offs and follow-up

- Free text never survives a reload before completion, by design. There is no
  encrypted draft API in this milestone; "continue later" works within the
  same open tab. No plain-text localStorage.
- Taxonomy explanations and keyword fallback are English-only. They are
  conservative and non-diagnostic; multilingual review is parked.
- The existing safety ML model may classify ordinary exam-related phrases as
  HIGH under default settings (observed with `My exam is tomorrow` and the
  sample thought). We do **not** suppress a HIGH verdict in this feature.
  The real-browser verification explicitly ran with `SAFETY_ML_ENABLED=false`
  (supported rules-only mode); ML false positives need separate measured
  evaluation before changing ensemble thresholds.
- API list is bounded to the latest 100 records; pagination is future work.

See [safety design](../safety-design.md#day-19-cbt-thought-record) and
[Day 19 verification](../verification/day19-cbt-thought-record.md).
