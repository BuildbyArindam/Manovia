# ADR 0045 — Personalised meditations: curated structure, linted passages, local render

Date: 2026-10-17. Status: accepted.

## Context

Day 45 adds a meditation surface: a script generator that composes a
personalised, read-aloud meditation from `{theme, duration, voice speed,
preferences, language}`; local voice rendering with pauses and a content-hash
cache; and per-section captions. The hard requirements are safety rules —
no trance/dissociation inductions, no medical claims, no promises, no
closed-eye instructions while the person may be walking or driving, a safety
preface, an eyes-open variant, and the ability to stop at any time — and
they must hold whether a passage was written by the curated catalogue or by
a model.

## Decisions

- **The safety frame is curated, forever.** The preface, stop cue, eyes
  line, breath cues, openings and closings all come from
  `backend/app/content/meditation/{en,hi,bn}.yaml`. The LLM may only
  replace individual *body passages* (at most two per script); it can never
  touch the frame. A curated-only composition is always reachable in one
  step, and the test suite pins that it passes every gate for all
  6 themes × 4 contexts × 3 languages.
- **Two gates, both deterministic and offline.** Every assembled script
  passes the meditation linter (`app/services/meditation/lint.py` —
  forbidden-content patterns, negation-aware, per-language marker sets,
  stop-cue and driving-warning presence, word budget, sentence length,
  exclamation cap) **and** the full Day 14 `RuleBasedOutputGuard`. A
  candidate LLM passage is linted on its own before it may enter the
  script; a rejection gets exactly one stricter retry, then curated copy
  stands in. Failures raise with lint codes only — never with script text.
- **Closed vocabulary end to end.** Request models are frozen with
  `extra="forbid"` and Literal fields: unknown theme, tag or context is a
  422, never a silent default, because a silent default is how a
  closed-eye script would reach somebody driving. Preferences are ten
  closed tags (the `PREFERENCE_TAGS` frozenset, cross-checked in a test
  against the Literal the model validates); personalisation never flows
  through free text.
- **`eyes_open` is validated, not inferred.** Closed eyes are only offered
  for `context="seated"` (a model validator rejects the combination);
  walking/driving force the eyes-open variant and the preface variant that
  carries the pull-over warning. The linter re-checks the final text
  (negation-aware, so "no closing your eyes" is not a hit).
- **Determinism is the cache key.** The same request + seed always
  composes the same script; seedless requests derive their seed from a
  hash of the request. The script's `content_hash` covers language,
  resolved theme, duration, speed, context, eyes state, preferences, seed
  and transcript. The audio endpoint re-derives that hash from the script
  the client sends back: a tampered transcript is a 422, not a render.
- **The LLM is optional and disabled by default.** With the offline Fake
  provider (or none) no LLM call is made at all. The API enables LLM
  passages only when a configured provider exists that is not `fake` — the
  Fake's canned reply is a test double and must never reach a script.
  Provider failures fall back silently to curated copy (the exception
  class, never the text, may be logged).
- **Voice rendering is section-wise local TTS.** Piper (or the offline
  fake) renders one section per call; the script's pause markers become
  silence inserted by the renderer — that is the "SSML-like pauses"
  mechanism, since the local binary takes plain text. Renders are cached
  by `sha256(provider | model | language | speed | transcript)` as
  `<key>.render.wav` + `.json` under `MEDITATION_TTS_CACHE_DIR`
  (default `~/.cache/manovia/meditation-tts`). Cache failures are misses,
  never errors. A missing Piper model is an honest 503, not silence.
- **Captions carry estimated timing, said plainly.** Section timing comes
  from the 115 wpm × speed model the composition used; `timing_basis` is
  `"estimated"`, and the script's `estimated_duration_s` is disclosed
  wherever the nominal duration would be over-promised.
- **The word floor is the honest maximum.** The linter's floor is
  `min(0.75 × budget, curated_max)` where `curated_max` is what the theme's
  curated pool can actually fill in that language. A thin pool (or a long
  offline script the pool cannot fill) relaxes the floor instead of
  rejecting every safe script; the composer tops up with vetted breath
  cues (up to three) before accepting a short script. The ceiling is
  `1.2 × budget`, enforced by trimming.
- **Nothing is stored.** Scripts, audio and captions live in the request
  and the response; the server keeps no transcript, no audio, no
  favourites. (The library of saved favourites is a client-side feature;
  the content hash is its identity.)

## Amendment (Day 45 verification)

The live API verification (step 2 of the Day 45 brief) found a gap in the
content hash: it was a function of the top-level `transcript` field, but the
renderer reads the **sections** (their text, roles, pauses). A client that
rewrote a section's text while keeping the original `transcript` and
`content_hash` would get the tampered words rendered and spoken. The hash
basis is now `meditation-v2` and covers every section's role, source, text
and pause, so it is a function of exactly what gets spoken; the
render-cache key is derived from that hash plus engine and voice.
Regression tests: `test_tampered_sections_change_the_content_hash` (unit)
and `test_audio_rejects_a_tampered_section` (integration).

## Consequences / limits

* A 10-minute offline script is shorter than nominal when the curated pool
  cannot fill it; the estimate is disclosed rather than padded with filler.
  Long scripts are best served with a configured LLM.
* Hindi and Bengali content are AI-draft translations, flagged in
  `translations/STATUS.md`; the linter's Indic pattern sets are a
  backstop, not a substitute for native review.
* Caption timing is an estimate (the real Piper render differs by a few
  seconds); the field says so.
* The render cache is process-agnostic (a file dir) but per-deployment;
  clearing it costs render time, not correctness.
