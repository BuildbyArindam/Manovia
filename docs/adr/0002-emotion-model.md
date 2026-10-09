# ADR 0002 — Emotion model and offline degradation

- Date: 2026-10-09
- Status: accepted for development; real-model validation pending

## Choice and licence

Recommended `EMOTION_MODEL_ID=SamLowe/roberta-base-go_emotions`:
https://huggingface.co/SamLowe/roberta-base-go_emotions . The published model
card identifies MIT licensing. GoEmotions data is Apache-2.0 (Google Research:
https://github.com/google-research/google-research/tree/master/goemotions).
The repository's network policy blocks Hugging Face, so the model card and
weights could not be independently fetched here. Reconfirm the model card,
licence and upstream revisions before production distribution.

The identifier comes only from environment configuration, not application
code. `EMOTION_PROVIDER=fallback` is the offline default; `fake` is for tests;
`hf` requires a nonempty model id and the optional `nlp` dependency extra.
Install with `pip install -e 'backend[nlp,dev]'` from the repository root.

## Interpretation

RoBERTa GoEmotions has 28 independent labels. Request all sigmoid scores and
map related labels to nine internal emotions using maximum score per group,
not sum. Joy includes admiration/love/optimism; sadness includes grief;
anger includes annoyance/disgust; nervousness maps to anxiety;
embarrassment/remorse to shame; relief to calm. Cognitive/unmapped-affect
labels map to neutral. No GoEmotions loneliness label exists: its HF score
stays zero, rather than claiming sadness proves loneliness. Unsupported model
label sets fail into fallback rather than silently trusting LABEL_0 outputs.

Valence/arousal are fixed weighted projections of emotion scores, **not**
validated psychological measurements. Independent scores need not sum to one.
English training data does not justify Hindi/Bengali performance: detected
hi/bn/other routes to the small keyword fallback (Hinglish returns hi).

## Reliability and privacy

CPU device -1, PyTorch, lazy initialization once per analyzer, serialized load
and inference, max 128 tokens by default, batch size 4. Inputs are bounded to
10,000 characters and token-truncated by the pipeline. API payloads exceeding
that limit return 422; empty input is neutral. This diagnostic route is not
registered in production and is not connected to chat or any LLM.

One daemon inference worker at a time per service; deadline defaults to one
second. Failed loads are remembered. Timeout/busy/error use keywords, log only
curated reason codes (never provider exception strings). Native inference
cannot be killed by a Python timeout; the running worker retains bounded input
until completion, while new requests fall back without queuing. A process
restart resets a hung worker or failed load. Cache keys are SHA-256 over language
plus original text; bounded LRU stores only keys and copied results. Degraded
results remain cached until eviction/restart. Hashes are not anonymisation and
could be dictionary-attacked; they appear only in debug cache-hit logs.

`SentimentAnalyzer` implements a small polarity idea, not a claimed legacy
port: `legacy/chatbot-1` is missing from this checkout and the GitHub legacy
path returns 404. Lexicons include a few Hindi/Bengali terms, token boundaries
and simple negation, but miss sarcasm, complex negation and mixed-language nuance.

## Verification

Normal `make test` excludes `model` and is fully offline. Contract and integration
tests use Fake; pipeline fixtures test labels, batching, failure and lazy/thread
behaviour. `cd backend && pytest -m model -q` is opt-in; missing extras or
unavailable weights are explicit skips, not real-model passes. Run locally with
network and `MODEL_TEST_ID` if selecting a different compatible model. Benchmark
20 warmed sequential uncached calls locally and report model p50/p95; fixture
latencies are not model performance. Set `EMOTION_CACHE_SIZE=0` when measuring
inference, and increase `EMOTION_TIMEOUT_SECONDS` for cold download/loading.
