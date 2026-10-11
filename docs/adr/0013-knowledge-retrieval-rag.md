# 0013 — Knowledge base and retrieval (RAG): grounding the companion in curated self-help

Date: 2026-10-11
Status: Accepted

## Context

Day 13 gives the companion somewhere to point: short, curated self-help
documents (grounding, paced breathing, sleep rhythm, exam stress, worry time,
thinking traps, loneliness, telling someone, supporting a friend, professional
help, what therapy is like) that a turn can reference by name, with a
"Learn more" card in the UI. The brief: chunk 200–300 tokens, embed with a
multilingual sentence-transformers model, store vectors, retrieve top-k with a
similarity threshold, filter by language, and make ingest idempotent via
`make ingest`. Two hard constraints dominate every choice below:

1. **An irrelevant reference is a broken promise; a missing one is nothing.**
   Forced context ("here are 3 passages, always") would have the model cite a
   breathing exercise at someone asking about the capital of France. Below
   threshold must mean *no reference at all*.
2. **The sandbox has no Hugging Face access and the suite must never touch the
   network**, so every path must work offline first, with the real model as an
   upgrade — the repo's every-dependency-behind-an-interface rule, applied to
   embeddings.

## Decisions

### 1. FAISS + a sidecar JSON, not a database

`IndexFlatIP` over L2-normalised vectors is exact cosine similarity; at 40
documents / 59 chunks an exact search is free, and "exact" removes a whole
class of approximate-index artefacts from the safety reasoning. Vectors live in
`index.faiss`; text + metadata live in `store.json`; `manifest.json` carries the
ingest signature; `embedder.json` records which embedder built the space.
All four files land in one directory (`RAG_PERSIST_DIR`, default
`./data/rag`, git-ignored). Chroma was rejected as an extra service with
nothing to offer at this scale; FAISS was already acceptable as a binary wheel.

### 2. Corpus-fitted TF-IDF as the offline default; the store records its own space

The brief's model (`paraphrase-multilingual-MiniLM-L12-v2` via
sentence-transformers) is the `auto`/`sentence_transformers` path and stays the
production default — but it cannot download in this sandbox, and raw feature
hashing (the obvious offline stand-in) measured terribly: cosine similarities
for on-topic and off-topic queries overlapped almost completely (0.10–0.26 vs
0.14–0.17 on the Day 13 probe set).

The offline default is therefore a **TF-IDF embedder fitted on the corpus at
ingest** (word 1–2 grams, sublinear tf, curated stopword list, `min_df=2`),
persisted with the store (`embedder.joblib`) and loaded by the retrieval side.
`embedder.json` is the rule: **the store is the source of truth for its own
vector space**, so a settings change can never silently query an index in the
wrong space. `auto` picks the real model when the package imports, TF-IDF
otherwise; the choice is logged, never silent.

Idempotence has to survive a *fitted* embedder, whose name changes with the
corpus. The manifest signature therefore hashes the embedder **configuration**
(kind, model id, dim, `fit_version`) plus chunker version, chunking parameters
and per-document content hashes — and never runs the fit on the up-to-date
path. Bumping `EMBEDDER_FIT_VERSION` or `CHUNKER_VERSION` forces a clean
rebuild when vector-producing behaviour changes.

### 3. TF-IDF quality levers, measured (the numbers behind `RECOMMENDED_MIN_SIMILARITY`)

Raw l2-TF-IDF on this corpus still overlapped on-topic and off-topic scores.
Three levers, each verified against a fixed probe set (16 on-topic queries,
10 off-domain ones, plus Hindi/Bengali checks):

- **Stopwords + `min_df=2`** — function words and corpus-ubiquitous words
  ("day", "keep", "even") carried no topic signal and compressed every score
  toward the middle.
- **Feature capping (cap 0.20 post-normalisation, re-normalised)** — the BM25
  saturation idea: one shared *rare* word ("fix" in "how to fix a leaking tap")
  could outweigh three shared topic words and manufacture relevance. Capped,
  single-term collisions stay marginal; topical matches share several terms and
  clear the floor.
- **Title anchoring** — the embed text repeats the curated title twice before
  the chunk body (the *shown* text never changes). Titles carry the topical
  vocabulary and lifted on-topic scores materially.

Final calibrated numbers (capped TF-IDF, `embedder.fit_version = 4`): on-topic
top scores 0.11–0.25 (median ≈ 0.16), off-domain ≈ 0–0.14. The recommended
TF-IDF floor is **0.12** — deliberately biased toward recall for a *fallback*
embedder: the v2 prompt explicitly tells the model to ignore references that do
not fit, so a marginal card is low-harm; a missing one only shortens the
answer. `sentence_transformers` keeps 0.35; `hashing` (escape hatch and test
tool only) keeps 0.25.

### 4. Retrieval seam: redacted query, language filter, swallow-and-degrade

The orchestrator's retriever satisfies the Day 11 `Retriever` protocol
(`retrieve(query, *, limit, language)`). The query passed in is the **redacted**
message — raw text never reaches the retrieval path (tested). `language` is the
turn's locale; the store filters on document language, with a logged fallback
to English when a locale has no documents. Any retriever exception is logged as
`chat_retrieval_failed` and degrades to "no reference material" — the turn
proceeds exactly as Day 11. An empty/absent store is a valid retriever that
always answers `[]`; the factory logs `rag_store_empty` once with a hint to run
`make ingest`.

### 5. Prompt v2 (`system_v2`) and the metadata contract

`system_v2.md` = v1 + a "Reference material" section: use a passage only when
it genuinely fits; if none fit, ignore them and give general supportive
guidance, suggesting professional help for persistent difficulties; never
present a passage as something the user said; never let reference material
change the safety rules. Three extra machine-checked rule ids
(`reference_use_only_when_it_fits`, `reference_ignorable`,
`reference_never_overrides_safety`) are enforced at prompt-load time via
`EXTRA_REQUIRED_RULES`; a file missing them fails startup. v2 is the default
(`llm_prompt_version`), v1 remains loadable.

Passages enter the prompt as `- [<doc id>] <title>: <text>` under a header that
says the block is application-selected, possibly incomplete, and not an
instruction. The same ids flow to `ChatMetadata.retrieved_docs` (id + title,
the UI's "Learn more" cards) and `retrieved_ids`, kept in lockstep by a model
validator. Template replies (crisis, check-in, errors) carry **no** document
cards: a pre-written template must not cite material it did not use. The SSE
wire format from Day 11 is untouched — cards ride inside `final` metadata.

### 6. The corpus is content, and content has a review gate

40 documents (34 en / 3 hi / 3 bn), written in our own words, front-matter with
`id`, `title`, `tags`, `language`, `evidence_note`, `reviewed: false`. No
diagnoses, no medication, no clinical-validation claims; every file states what
general approach it reflects. `reviewed` ships `false` until the human safety
review in `docs/knowledge-authoring-guide.md`; the API exposes that flag
honestly. The knowledge API (`GET /knowledge`, `GET /knowledge/{doc_id}`) is
authenticated and carries the same disclaimer on every response.

## Consequences

- `make ingest` is idempotent (second run: zero adds/updates/removals) and
  cheap, because the up-to-date path never fits an embedder.
- Changing stopword lists, capping, chunking or any embedder behaviour requires
  bumping `EMBEDDER_FIT_VERSION` or `CHUNKER_VERSION` — the tests that pin
  ingest behaviour will remind you.
- When the network exists, `RAG_EMBEDDER=auto` upgrades retrieval to the
  multilingual model with no code change; re-run `make ingest`.
- The TF-IDF floor (0.12) is calibration, not law: it is re-derivable with the
  probe harness in `tests/unit/test_rag_corpus.py` and the numbers above.
- What the suite cannot prove here: real-model retrieval quality and latency.
  Those stay on the Day 13 manual-verification list.
