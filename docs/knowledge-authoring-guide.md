# Knowledge authoring guide

How to write and review documents for the Manovia knowledge base
(`backend/app/content/knowledge/`). These are the rules for *any* self-help
content that retrieval can attach to a conversation, and the checklist a human
 reviewer walks before a document's `reviewed` flag becomes `true`.

## What these documents are

Short, kind, plain-language self-help pieces a person can open from a "Learn
more" card, or browse via `GET /api/v1/knowledge`. They are **general
information**, not therapy, not assessment, and not advice about a specific
person. The companion may reference a technique by name ("something that helps
some people is naming five things you can see"); the document is where the
person reads more, on their own terms.

## File format

One Markdown file per document, with YAML front-matter:

```markdown
---
id: sleep-basics-rhythm
title: "A steady sleep rhythm"
tags: [sleep, rhythm, nights]
language: en
evidence_note: reflects sleep-hygiene advice widely taught in CBT-informed self-help
reviewed: false
---

# A steady sleep rhythm

...the body...
```

Field rules:

- `id` — kebab-case, stable forever. It is the primary key for retrieval
  metadata, chat metadata (`retrieved_ids`), the API and the chunk ids
  (`<id>#00`). Renaming an id is a content migration, not a refactor.
- `title` — double-quoted; avoid quote characters inside (YAML).
- `tags` — 2–5 lowercase single words. They are filterable metadata
  (`where={"tag": ...}`) and appear in the API summary.
- `language` — ISO 639-1 (`en`, `hi`, `bn`). Retrieval filters on it; a query
  in one language never surfaces documents in another.
- `evidence_note` — one honest sentence about the *general approach*
  ("widely taught in CBT-informed self-help"). Never a claim that **this
  document** is validated, tested, or clinically approved.
- `reviewed` — `false` until a human has done the checklist below. New
  documents always ship `false`. The API publishes this flag as-is.

## Voice and style

- **Plain, warm, short.** Aim for 250–450 words. Second person ("you"), present
  tense, no jargon without a same-sentence explanation ("thinking traps —
  habits of thought that make a bad moment feel final").
- **Concrete beats abstract.** "Put the phone face-down across the room" beats
  "reduce stimulation". One technique per document; a document is a bite, not a
  programme.
- **Own words only.** Never copy sentences from books, apps or websites. The
  plagiarism sanity check (>12-word verbatim strings against known sources)
  runs at verification time, but the rule is the author's, not the tool's.
- **Say "some people", not "you will".** These techniques help some people some
  of the time. No promises of outcomes; never "this will calm you in 5
  minutes".
- **Permission-giving, never commanding.** "If it feels possible, try…" rather
  than "You must…".
- **One gentle cross-reference at most** ("related: telling someone you
  trust"), by referring to the other document by name.

## Safety rules (hard lines)

1. **No diagnosis.** Never name disorders as applicable to the reader, never
   "if you have depression…". Describe experiences ("nights when sleep will not
   come"), not conditions.
2. **No medication.** Nothing about doses, brands, classes, or whether to
   start/stop anything. Not even to say "talk to your doctor about medication"
   — the professional-help documents cover seeking care generally.
3. **No clinical-validation claims.** "Based on widely taught CBT techniques"
   is the ceiling. No "proven", "clinically shown", "therapy-grade".
4. **No crisis content.** Documents are for the non-crisis band. If a topic
   drifts toward self-harm, it belongs to the crisis team's copy, not here.
   Never include crisis instructions, statistics, or methods.
5. **No "reach out to emergency services" boilerplate** — that framing lives in
   the orchestrator's crisis path, which is tested; duplicating it here invites
   drift. A document *may* say "if this feels heavier than self-help, a
   professional is the right next step" and name general routes (GP,
   counsellor, helpline).
6. **No data collection or tech instructions.** No "track yourself in this
   app"; the corpus is self-contained.
7. **English, Hindi and Bengali only** (the languages Manovia ships). A Hindi
   or Bengali document must be *written in* that language, not machine-folded
   from English — idioms differ, and retrieval matches on real vocabulary.

## Substance checks

- Does the technique survive being read *alone*, without the chat that led
  here? (It will be opened cold from a card.)
- Is the first paragraph the actual topic (the retrieval embedder weights the
  title and the opening heavily)?
- Would this document feel respectful to someone who is not a student, not a
  teenager, not urban, not employed? Default to the most general framing.
- Does it avoid moralising about sleep, food, screens, productivity?
- Is every instruction safe for a reader who might try it at 3 a.m., alone?
  (No cold baths for someone shaky; no "call your friend right now".)

## The human review checklist (before `reviewed: true`)

Walk every item; a single "no" keeps the flag `false`.

1. [ ] Front-matter complete and valid; `id` matches the filename; `language`
       matches the body language.
2. [ ] Every hard line above holds (diagnosis / medication / validation
       claims / crisis content / boilerplate).
3. [ ] Tone check: read it as if you are the person at their worst moment.
       Nothing lecturing, nothing false-promising.
4. [ ] Own words: spot-check 3 distinctive sentences in a search engine; no
       near-verbatim source appears.
5. [ ] Technique accuracy: the exercise as written matches how it is usually
       taught (box breathing is 4-4-4-4; grounding is 5-4-3-2-1).
6. [ ] `evidence_note` is honest and appropriately modest.
7. [ ] The document renders: headers, lists, no broken YAML
       (`python -c "from app.content.knowledge import load_knowledge_docs; load_knowledge_docs()"`).
8. [ ] Grep the file for forbidden vocabulary: medicine names, disorder names
       used diagnostically, "proven", "clinically", "guaranteed".
9. [ ] Reviewed by a human who is not the author.
10. [ ] Flip `reviewed: true` in the same commit as the review notes, re-run
        `make ingest`, and mention the document id in the PR.

## Verification commands

```bash
make ingest          # must be a no-op on second run
python3 -m pytest backend/tests/unit/test_rag_corpus.py -q
```

The corpus test pins the behavioural contract: an on-topic question retrieves
the right document, an off-topic one retrieves nothing, and ingest is
idempotent. If you add a document and the corpus test still passes, retrieval
quality held.
