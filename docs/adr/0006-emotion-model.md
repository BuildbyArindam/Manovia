# 0006 - Emotion model: choice, mapping, and licence

- Status: accepted (Day 6)
- Date: 2026-10-09
- Deciders: Manovia maintainers
- Supersedes: none
- Related: [0001 monorepo and stack](0001-monorepo-and-stack.md), [0002 backend skeleton conventions](0002-backend-skeleton-conventions.md)

> **Numbering note.** The Day 6 brief asked for this document at
> `docs/adr/0002-emotion-model.md`. `0002` was already taken on `main` by
> *Backend skeleton: configuration, logging, and error conventions*, so this ADR
> is `0006` - the next free number - rather than overwriting an accepted
> decision. The content is what was asked for.

## Context

Day 6 adds an NLP service that reads one message and reports an emotion plus a
valence/arousal pair. Everything downstream (mood tracking, journal summaries,
exercise selection, and eventually the safety rules) consumes that output, so
the shape of the answer matters more than which model produced it.

The constraints that actually drove this decision:

1. **A small, auditable taxonomy.** Nine labels - `joy, sadness, anger, fear,
   anxiety, shame, loneliness, calm, neutral`. A product feature cannot act on
   27 GoEmotions labels, and a human reviewer cannot audit them either.
2. **CPU-friendly.** This must run on a small container with no GPU.
3. **The model is a setting, not a constant.** AGENTS.md: never hard-code model
   names. `EMOTION_MODEL_ID` decides; changing the model must not be a code
   change.
4. **The model is the least reliable component.** It may be absent (the `nlp`
   extra is optional), blocked (no hub access), slow, or throw mid-request. None
   of those may produce an error page in front of someone who just said
   something difficult.
5. **The test suite runs fully offline.** AGENTS.md: every external dependency
   sits behind an interface with a Fake.
6. **Licence must be compatible with the product** (MIT, see `LICENSE`), and
   must be recorded rather than assumed.
7. **Users type English, romanised Hindi and Bengali.** Whatever model is chosen
   must not silently pretend to understand languages it was never trained on.

## Decision

### 1. The model

Default `EMOTION_MODEL_ID=SamLowe/roberta-base-go_emotions`: a RoBERTa-base
(~125M params) multi-label classifier over the 27 GoEmotions labels plus
`neutral`. Chosen because:

- it is **multi-label**, which matches how people actually write ("scared and a
  bit excited"), and because `top_k=None` returns every head's probability;
- GoEmotions' 27 labels map onto **seven** of our nine, more than any Ekman
  model can (see below);
- it is MIT-licensed (see [Licence](#licence));
- it is a single `transformers` pipeline with no custom code, so swapping it is
  a config change.

It is a starting point, not a commitment. `EMOTION_MODEL_ID` is the only place
it appears in the product; `LABEL_MAP` in `app/services/nlp/hf.py` is the only
place its labels are interpreted.

### 2. One internal taxonomy, one mapping table

`LABEL_MAP: dict[str, tuple[str, float]]` folds a model label into
`(internal emotion, weight)`. The weight says how strongly a label implies the
emotion: `disgust` is anger-ish (0.8), `relief` is calm-ish (0.9), `confusion`
is mostly just neutral (0.4). Both label sets the candidate models use are
covered - GoEmotions (27) and the Ekman set (7) - plus `positive`/`negative` for
sentiment-flavoured checkpoints.

Two rules worth stating because they are easy to get wrong:

- Per internal emotion the **maximum** mapped probability is used, not the sum.
  A multi-label model fires on several labels of one family (`sadness` *and*
  `grief`); summing would invent confidence the model never expressed.
- An **unmapped label is dropped and logged once** (`emotion_label_unmapped`),
  never guessed at. A new checkpoint with a new head shows up in the logs
  instead of silently changing the output.

`valence` and `arousal` are never taken from the model. They are derived from
`EMOTION_DIMENSIONS` - one hand-set `(valence, arousal)` anchor per label - so
the numbers mean the same thing whichever analyzer answered.

### 3. Loading and inference

- **Lazy, thread-safe, once.** The first call loads under a lock with a
  double-check; a failed load is *remembered*, so a broken model id costs one
  warning and one failed import rather than one per request.
- **CPU only** (`device=-1`), small batches (`EMOTION_BATCH_SIZE=8`), and
  `EMOTION_MAX_LENGTH=256` tokens with `truncation=True`. Long input is also
  pre-cut by character count before tokenising, so a 10,000-character paste
  cannot blow up tokenisation cost.
- **`transformers`/`torch` are imported inside the loader.** The suite, CI, and
  any deployment without the `nlp` extra can import the module and get the
  fallback. They are an optional extra, not a base dependency.

### 4. Graceful degradation

`AnalyzerChain` puts the model first and the lexicons behind it
(`keyword`, then `sentiment`), and falls through on either of two triggers:

- **failure** - after `EMOTION_FAILURE_THRESHOLD` (2) failures a circuit opens
  for `EMOTION_COOLDOWN_SECONDS` (60), so a broken model is skipped rather than
  retried per request;
- **slowness** - an EMA of the model's own latency; above `EMOTION_SLOW_MS`
  (1500) the chain stops routing to it, because a companion that stalls
  mid-conversation is worse than one that answers more crudely.

The chain **never raises**: if everything fails the caller gets a neutral result
with `analyzer="unavailable"`. Failed-everything results are not cached, so a
transient outage cannot become sticky.

A zero-confidence `neutral` ("I looked and found nothing") also falls through;
a *confident* neutral stops the chain.

### 5. Fallbacks

- `KeywordFallbackAnalyzer` - a nine-label lexicon (English + romanised Hindi +
  Banglish + Devanagari/Bengali script) with negation, intensifiers and
  emphasis. It runs first among the fallbacks because it can name all nine
  emotions. Its `confidence` is capped at 0.6: a keyword hit is not a
  probability.
- `SentimentAnalyzer` - the polarity-lexicon idea from the legacy chatbot (see
  [Known issues](#known-issues) - the legacy source is not in this repository,
  so this is a port of the *idea*). It runs second because it can only band
  polarity into joy/calm/neutral/sadness, but it recognises words the emotion
  lexicon misses and returns a continuous valence.

### 6. Caching and privacy

Results are cached in a per-process LRU keyed by
`sha256(variant | lang | whitespace-collapsed text)`. The plaintext is stored
nowhere; logs carry a 16-hex-character fingerprint and a length instead
(AGENTS.md rule 5). The model id is part of the key, so swapping models cannot
serve the previous model's answers. Case is preserved, because "I am FINE" and
"i am fine" are read differently.

## Alternatives considered

| Model | Labels | Size | Licence | Why not the default |
| --- | --- | --- | --- | --- |
| **`SamLowe/roberta-base-go_emotions`** | GoEmotions 27 + neutral | ~125M | MIT (verified, see below) | **Chosen.** |
| `j-hartmann/emotion-english-distilroberta-base` | Ekman 6 + neutral | ~82M | commonly cited as MIT - **not verified here** | Smaller and faster, but only 7 heads: `anxiety`, `shame` and `loneliness` could never be reported by the model at all. Good choice if CPU budget becomes the binding constraint. |
| `bhadresh-savani/distilbert-base-uncased-emotion` | Ekman 6 | ~66M | commonly cited as Apache-2.0 - **not verified here** | Smallest, but the same Ekman blind spot, and trained on a single (Twitter-era) dataset. |
| A multilingual model (e.g. XLM-R fine-tunes) | varies | 270M+ | varies | Would genuinely cover Hindi and Bengali, but is 2x the CPU cost and the good checkpoints in this space are scarce and inconsistently licensed. Deferred; see Known issues. |
| A hosted API (e.g. a cloud emotion endpoint) | varies | n/a | proprietary | Sends user text to a third party. Incompatible with a privacy-first product. Rejected outright. |
| Lexicon only (no model) | 9 | 0 | n/a | Shipped as the fallback. Fine as a floor, too blunt as the primary: no context, no compositional understanding, and sarcasm reads as its opposite. |

## Licence

- **`SamLowe/roberta-base-go_emotions`: MIT.** Verified from three independent
  sources in this sandbox (the hub itself is not reachable from it): a mirror of
  the model card that states "MIT, as the original model (LICENSE, Copyright (c)
  2023 Sam Lowe)", a model-registry listing carrying the `license:mit` tag, and
  a hosted-endpoint catalogue listing "Licence: MIT".
- **The GoEmotions dataset** (Demszky et al., 2020) the checkpoint was trained on
  is published by Google Research and is generally distributed as Apache-2.0.
  **This was not verified here** - confirm on the dataset repository before any
  commercial use, since the model's licence does not by itself settle the
  training data's terms.
- **`transformers` (Apache-2.0) and `torch` (BSD-3)** are optional runtime
  dependencies under the `nlp` extra; neither is vendored or redistributed.
- **`langdetect` (Apache-2.0)** is a base dependency; it ships its language
  profiles in the wheel and makes no network calls.
- Everything Manovia wrote here - the taxonomy, the label map, the lexicons, the
  chain - is under the repository's own MIT licence.

Re-check the model card on every model-id change. This ADR records the licence
of the default, not a standing approval for whatever `EMOTION_MODEL_ID` is
pointed at next.

## Consequences

**Good**

- Swapping the model is an env change plus (possibly) new `LABEL_MAP` rows.
- The service answers with no model installed, no network, or a model that
  throws - and says which analyzer answered (`analyzer`, `model`,
  `confidence`), so a degradation is visible in the response and in the logs.
- The offline suite covers the real pipeline (a tiny locally-built model, no
  download) as well as every fallback and failure path.

**Costs**

- ~500 MB of model weights and a cold-start load on first request. Pre-warming
  at startup is a deploy decision, not made here.
- A second source of truth for label meaning (`LABEL_MAP`) that must be reviewed
  on every model change. The `emotion_label_unmapped` warning is the tripwire.

## Known issues

- **The default model is English-only.** Hindi and Bengali text is detected
  (`language.py`) and routed, but the model reads it as noise. Today those
  messages are effectively served by the lexicon fallbacks, which do carry
  romanised Hindi, Banglish and Indic-script terms. A multilingual checkpoint is
  the real fix and is in the parking lot.
- **`loneliness` has no head in GoEmotions.** It can only come from the
  lexicons, so a message like "nobody has called in weeks" is caught by a word
  list or not at all. `shame` and `anxiety` map from `embarrassment`/`remorse`
  and `nervousness` respectively, but thinly.
- **The legacy `chatbot-1/` source is not in this repository.** ADR 0001 says to
  keep it under `legacy/chatbot-1/`; it has never been supplied. `SentimentAnalyzer`
  therefore ports the *idea* (two weighted lists, a running score, a squashed
  `[-1, 1]` result) rather than the code. Re-diff it against the real source when
  the source arrives.
- **Accuracy is unmeasured.** No eval set is wired up yet (`evals/` holds
  datasets and a reports directory but no harness). The numbers in Day 6's
  verification are latency and behaviour, not accuracy.

## What must be checked by hand

1. `pytest -m model -q` with the `nlp` extra installed and network access: the
   real checkpoint downloads, loads, and every one of its labels is in
   `LABEL_MAP` (the test fails loudly on an unmapped head).
2. The GoEmotions dataset licence, as above.
3. Latency on the target CPU shape. `EMOTION_SLOW_MS=1500` was set from
   reasoning, not measurement; re-tune against real p95.
4. A read of `LABEL_MAP` by someone who knows the product's tone. It is the one
   place where a modelling choice becomes a product claim.
