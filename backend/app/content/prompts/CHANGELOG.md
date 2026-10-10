# Prompt changelog

Every prompt under `app/content/prompts/` is versioned by filename. The file
name **is** the version string (`system_v1.md` → `LLM_PROMPT_VERSION=system_v1`),
and `app/services/llm/prompts.py` SHA-256 hashes it at load time so a prompt
change is visible in logs and testable in a test.

Rules for changing a prompt:

1. **Never edit a shipped prompt in place.** Add `system_v2.md` next to it and
   change `LLM_PROMPT_VERSION`. The old file stays reviewable, and rolling back
   is a config change rather than a revert-under-pressure.
2. Bump the version for *any* wording change, including a typo: the hash is
   what makes output reproducible, and two different prompts must never share a
   version string.
3. A new version needs a changelog entry (below) **and** a test in
   `tests/llm/test_prompts.py` that states the rule the change serves. A prompt
   edit without a test is a prompt edit nobody can defend.
4. The loader rejects unknown `{placeholders}` and refuses to load a file whose
   required safety rules are missing, so a careless edit is a startup failure,
   not a silent change in how the product talks to somebody in distress.

---

## `system_v1` — 2026-10-10 (Day 10) — first version

The first shipped system prompt. Written against the Day 10 brief, rule by
rule, with the rule each block serves:

| block | rule it serves |
| --- | --- |
| "You are Manovia, an AI companion — not a therapist, not a doctor, and not a person" | disclose that it is an AI |
| "Aim for about {max_words} words" (default 120) | warm and brief |
| "Validate the feeling, not the outcome … never offer silver linings" | validate without toxic positivity |
| "Ask at most one gentle, open question" | at most one gentle question |
| "Never diagnose … Never discuss medication" | no diagnosis, no medication talk |
| "Never give instructions, methods, means, or details connected to self-harm" | no self-harm instructions |
| "encourage professional help and one trusted person" | encourage professional help and trusted people |
| "Never claim to be a therapist … never role-play as one" | decline the therapist/human role-play |
| "Reply in {language}" | respond in the user's language |

Rule ids enforced at load time (`REQUIRED_RULES` in
`app/services/llm/prompts.py`, each with a test in `tests/llm/test_prompts.py`):
`discloses_ai`, `brief`, `validates_without_toxic_positivity`,
`at_most_one_question`, `no_diagnosis`, `no_medication`,
`no_self_harm_instructions`, `encourages_professional_help`,
`declines_roleplay`, `responds_in_user_language`.

Decisions taken while writing it:

- **The AI disclosure is the first sentence, not a footer.** Somebody who only
  reads the first line must still know.
- **"Never say 'I understand'" is explicit.** Generic models produce it
  constantly; it is the single most common unearned intimacy in this domain.
- **Brevity is expressed as a budget, not a vibe**, so it is a setting
  (`llm_max_words`) rather than an argument in a prompt review.
- **The crisis instruction is deliberately thin.** Day 8's rules engine and
  Day 9's ensemble already own the crisis path with pre-written, localised
  copy; at high risk the model is not consulted at all. The last paragraph
  exists for the case where risk appears *mid-conversation* and the safety
  layer has not yet fired.

Known gaps, deliberately left for later:

- No few-shot examples. They cost tokens and they drift; add them only against
  a measured failure.
- No per-language tuning. `{language}` asks for the same behaviour in every
  language; Indic-language prompt variants have not been written or reviewed by
  a native speaker (see PROGRESS.md parking lot).
