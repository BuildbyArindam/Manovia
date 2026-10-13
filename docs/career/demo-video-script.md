# 90-second demo video script

> Screen recording + voiceover. Nine shots, ~10 seconds each. The in-product
> walkthrough this is based on is [docs/demo-script.md](../demo-script.md).
> Record against a fresh guest account with synthetic content only — never
> real text. The crisis test phrase is scripted; keep it verbatim so the
> deterministic path is what viewers see.

| # | Time | On screen | Voiceover |
| --- | --- | --- | --- |
| 1 | 0:00–0:08 | App home, header with the **Beta** badge; cursor hovers the footer line "Portfolio demo — not a clinical service." | "This is Manovia — a mental-wellbeing self-help companion I built in 30 days. First thing on screen: what it is *not*. Not therapy, not a crisis service — it says so on every page." |
| 2 | 0:08–0:20 | Chat: type "I've been overwhelmed at work and sleeping badly." A warm, short reply appears. | "It's a chat-first companion. But the interesting part is what happens before the language model ever runs." |
| 3 | 0:20–0:34 | Chat: type the crisis test phrase. The crisis card renders instantly: pre-written copy + helplines. Point at the helpline numbers. | "A message that looks like crisis never reaches the model. Deterministic rules catch it first and answer with pre-written support and real helplines — the LLM is not allowed to improvise here. On the evaluation split, this pipeline catches every high-risk case." |
| 4 | 0:34–0:42 | Switch language to Hindi; send a Hinglish risk phrase; localised crisis card. | "That includes Hindi, Hinglish and Bengali — euphemisms too. I red-teamed this with 150 adversarial cases; the final suite passes all 150." |
| 5 | 0:42–0:52 | Mood check-in (five faces), then the journal; show the lock/encryption hint in the UI. | "Around the chat: mood check-ins, an encrypted journal, guided exercises, a CBT thought record. Entries are encrypted at rest, per field." |
| 6 | 0:52–1:02 | PHQ-9 screening; answer item 9 positively; support options appear **before** any score. | "The PHQ-9 uses the published wording — and if item nine is positive, support comes before any score does." |
| 7 | 1:02–1:12 | Settings → Privacy: export everything (file downloads), then the delete-account flow. | "Privacy is structural: export everything, delete everything, set your own retention. No raw text in logs, no identifiers to the model." |
| 8 | 1:12–1:22 | DevTools offline toggle; "Need help now?" still opens with cached helplines. | "It's an installable PWA — the 'Need help now' path works with no connection." |
| 9 | 1:22–1:30 | README quality section + `evals/reports/.../summary.md` gate table, ending on the repo URL. | "Every number I just said lives in the repo — eval reports, red-team report, and an honest list of limitations. It's a beta portfolio demo; clinical review is the gate before anything more. Link below." |

**Recording notes**

- 1280×800 browser window; reduced motion off; light theme for contrast on
  small players.
- No real personal text anywhere; use the synthetic lines above.
- If the live LLM is unavailable, the fallback reply is fine to show — it is
  part of the design (the provider chain ends in a safe local reply).
