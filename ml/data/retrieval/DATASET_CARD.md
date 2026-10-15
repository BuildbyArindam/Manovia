# Dataset card — Manovia retrieval eval set (Day 35)

> Written from [`ml/templates/DATASET_CARD.md`](../../templates/DATASET_CARD.md).
> This is the ground truth for every retrieval number quoted on Day 35: the
> Day-13 baseline, the contrastively fine-tuned dense encoder, BM25+dense hybrid
> fusion, the cross-encoder reranker, and the abstain calibration.

| | |
| --- | --- |
| Card version | `1.0` (2026-10-14) |
| Name | Manovia retrieval eval set |
| Status | **frozen for Day 35** — the `test` split is scored, never tuned on |
| Files | hand-written: `ml/data/retrieval/retrieval_queries.jsonl` (**committed**, sha256 `6bd4c3fc4c25879e…`, 36,535 bytes); expanded: `ml/data/retrieval/expanded.jsonl` (DVC output, not committed) |
| Catalogue entry | `ml/data/CATALOGUE.md` § 16 |
| Owner / authors | The repository owner; the sentences were drafted by an LLM assistant on the owner's instruction and the labels are owned by this repository |
| Licence | MIT — this repository's `LICENSE`. No third-party text |
| Personal data | **No.** Every row is invented. No real user message, transcript or scrape is used, quoted or paraphrased |
| Last verified | 2026-10-14 |

## 1. What it is

254 realistic user queries for Manovia's chat surface, each labelled with the
knowledge-document ids that would genuinely help that person (or with "no
document in this corpus would"). It exists to answer one question with a number:
*which retrieval setting should the chat pipeline use?* It is **not** a
general-purpose IR benchmark, not a clinical instrument, and not a sample of
real user traffic — it is 40 documents' worth of questions, written the way the
three target registers are actually typed.

## 2. Provenance

| | |
| --- | --- |
| Origin | authored in this repository (hand-written rows) + deterministic template paraphrase (synthetic rows) |
| Collection date | 2026-10-14 (Day 35) |
| Collection method | The 154 hand-written rows were written directly against the corpus in `backend/app/content/knowledge/` (all 40 documents are labelled at least once — `python -m manovia_ml.retrieval.data --check-corpus` fails if that stops being true). The 100 synthetic rows are produced by `python -m manovia_ml.retrieval.augment --target 100 --seed 20261013`, a DVC stage (`retrieval_expand`) |
| Platform terms | n/a — nothing was scraped |
| Personal data | no |
| Consent basis | n/a — invented sentences |
| Redistribution | the hand-written file is committed under MIT; the derived expansion is reproducible from it and is deliberately *not* committed (ML ground rule 3) |

## 3. Contents

| | |
| --- | --- |
| Languages | `en` 138, `hi-Latn` (Hinglish) 42, `hi` (Devanagari) 35, `bn-Latn` (romanised Bengali) 20, `bn` (Bengali script) 19 |
| Styles | `direct` 53, `keyword` 50, `no-answer` 55, `scenario` 32, `hinglish` 28, `vague` 25, `romanised` 11 |
| Size | 254 rows expanded (154 hand-written + 100 synthetic); 12 candidate paraphrases were rejected by the screens and are listed in `data/retrieval/dropped.jsonl` |
| Labels | 1–3 relevant document ids per answerable row (1 label: 33, 2: 128, 3: 34); 59 rows are `has_answer: false` with a `no_answer_reason` (`out-of-scope` 42, `corpus-gap` 17) |
| Fields | `id: str`, `query: str`, `lang: en\|hi\|hi-Latn\|bn\|bn-Latn`, `style`, `relevant_docs: [doc id]`, `has_answer: bool`, `source: handwritten\|synthetic`, `generator`, `seed_id`, `spot_checked: bool`, `no_answer_reason`, `note` |
| Hashes | `data/retrieval_splits/manifest.json` records, per split, the file sha256 **and** a content fingerprint (`split_fingerprint`: sha256 over id/query/labels/membership). `dvc.lock` records the outputs' hashes |

**Relevance means topical, not language-matched.** The corpus has only 3 Hindi
and 3 Bengali documents, and the production retriever falls back to English when
the requested language has nothing strong enough. So a Hindi query about
racing thoughts at night is labelled with the English sleep documents: that is
the correct answer for the person asking, and scoring it any other way would
measure the corpus gap instead of the retriever. The language-filtered posture
is reported separately, as a corpus-coverage finding.

**No crisis content.** Self-harm and crisis messages are deliberately absent:
the deterministic crisis gate runs before retrieval (AGENTS.md rule 1), so those
turns never reach a retriever, and a row that pretended otherwise would make the
abstain numbers look like a safety mechanism they are not. The one adjacent row
(`rt-na-018`, hearing voices) is labelled no-answer with
`out-of-scope: needs clinical assessment`.

## 4. Known biases

- **One author.** Every row was written by the same assistant-plus-owner pair, so
  the set reflects one view of how distress is phrased. Real users are messier,
  shorter, ruder and more repetitive.
- **No native-speaker review of the hi/bn rows yet** — the same limitation
  `translations/STATUS.md` records for product copy. Per-language numbers for
  `hi`, `bn` and `bn-Latn` carry that caveat, and the Hinglish rows are written
  by someone who knows the register but has not had it checked.
- **Synthetic rows are paraphrases of hand-written ones.** They inherit their
  labels (that is why they are safe) and they are lexically closer to the corpus
  than a real first draft would be. They are therefore kept **out of the test
  split entirely** and are only used for training pairs, dev tuning and a
  secondary dev measurement.
- **Topic mix follows the corpus, not the world.** Exams, sleep and breathing
  are over-represented because that is what the 40 documents cover; a real
  traffic distribution would have far more relationship, money and work
  questions — which is exactly what the 17 `corpus-gap` no-answer rows stand in
  for.
- **Labels are sets, and set boundaries are judgement.** Where two documents
  both help ("racing mind at night" → the sleep doc *and* the wind-down doc) both
  are labelled; a stricter single-label scheme would raise every Recall@1 and
  lower every Recall@5. Recall is reported both ways (`recall@k` set-based,
  `hit@k` any-relevant) so the reader can see the difference.

## 5. Fitness for Manovia

| use | allowed? | notes |
| --- | --- | --- |
| Training a shipped model | with conditions | the `train` split only, and only for the embedding/reranker fits; a model trained on 97 rows is not a general retriever |
| Evaluation / benchmark | yes | the whole point of the set; `test` for headline numbers |
| Threshold / fusion-weight tuning | **dev and calibration only** | tuning on `test` is a bug, and the fingerprints in the manifest make it visible |
| Research comparison only | yes | |
| Committed to Git | hand-written file **yes**, derived bytes **no** | the expansion and the splits are DVC outputs (`dvc repro`); weights never are |

## 6. Handling

- Storage: hand-written JSONL in Git (36 KB, no personal data); expansion and
  splits in `ml/data/retrieval/` and `ml/data/retrieval_splits/` (gitignored,
  DVC-tracked, rebuilt by `make ml-retrieval-data`).
- Retention: n/a — no personal data, nothing downloaded.
- Deletion: `rm -rf ml/data/retrieval_splits ml/data/retrieval/expanded.jsonl`
  (regenerable); the hand-written file is version-controlled history.
- Access: public repository.

## 7. Splits

Assignment is `sha256("<seed>:<id>")` bucketed into 100 (seed 20261013), so it
is a pure function of the row id — no RNG state, no file order. Hand-written
rows: test 0–34, dev 35–59, calibration 60–74, train 75–99. Synthetic rows can
only land in dev (0–49) or train (50–99).

| split | rows | hand-written | synthetic | no-answer | how chosen | may it be changed? |
| --- | --- | --- | --- | --- | --- | --- |
| train | 97 | 42 | 55 | 17 | seeded buckets | yes, with a new hash |
| dev | 85 | 40 | 45 | 23 | seeded buckets; **fusion weight is tuned here** | yes, loud |
| calibration | 20 | 20 | 0 | 7 | seeded buckets; **abstain thresholds are fitted here** | yes, loud |
| test | 52 | 52 | 0 | 12 | seeded buckets | **frozen** — hand-written only, scored and never tuned on |

Current fingerprints (`data/retrieval_splits/manifest.json`):
test `091a469c94b47c6b…`, calibration `1ce6ecf9c07afeeb…`, dev
`b9dc4cbbf98b10e9…`, train `c186b664c8f13716…`.

## 8. Review record

The synthetic rows were spot-checked as a batch, and the spot-check is the
reason this card exists in its current form: it found three real defects, all
fixed before the numbers in `ml/reports/day35-retrieval.md` were produced.

| reviewer | role | scope | date | outcome |
| --- | --- | --- | --- | --- |
| assistant (drafting) + repository owner | author | 25-row seeded sample of the 100 synthetic rows (`random.Random(7)` shuffle of the expanded set), plus all 34 no-answer rows and all 55 Indic-script rows | 2026-10-14 | 3 defects found and fixed; 4 rows accepted as awkward-but-plausible |

What the spot-check found:

1. **Indic tokenisation.** The screens tokenised with `[^\W_]+|[\u0900-\u097F…]+`,
   an *alternation*, which splits "बहुत" into "बह" + "ुत" — the keyword transform
   then emitted "बह ुत अक ेलापन लगत" as a Hindi paraphrase. Fixed to a single
   character class (`(?:[^\W_]|[\u0300-\u036f\u0900-\u097f…])+`, the construction
   `app/services/nlp/lexicon.py` arrived at on Day 6). The same bug exists in the
   shipped TF-IDF tokenizer, `app/services/rag/embedders.py`; Day 35 fixes that
   too (ADR 0035 §"Tokenisation"), because a multilingual retrieval day that
   leaves it in place would be measuring the bug.
2. **Context prefixes on out-of-scope rows.** "it has been like this for a couple
   of weeks. what's the weather going to be tomorrow in Kolkata" is not a
   sentence a person sends, and unrealistic no-answer rows flatter the abstain
   metrics. No-answer rows now draw from a restricted transform pool.
3. **An over-eager topic screen.** It rejected faithful paraphrases for
   containing "morning", "small" or "help" — words that appear in some document's
   title by accident — and it rejected a row for keeping the word "panic" that
   its own hand-written seed already contained. The screen now only fires on
   *curated tags* of other documents, and only for tokens the seed did not
   already have.

Accepted as imperfect (kept, and worth knowing when reading the numbers):
`rt-syn-0085` ("something can do on a crowded bus when freaking"),
`rt-syn-0074` ("brush off every nice thing people say about"),
`rt-syn-0050` ("talk myself cruelly than ever talk friend"),
`rt-syn-0100` (a doubled "but"). All four are keyword/depersonalise variants —
truncated fragments, which is a real register for a chat box, and all four keep
their seed's labels correctly.

Individual rows keep `spot_checked: false`: no per-row human sign-off exists, and
a boolean that says otherwise would be a lie. The batch review above is the
evidence, and it is why synthetic rows never reach the test split.

## 9. Sign-off

| check | who | date |
| --- | --- | --- |
| Catalogue entry complete and verified | author | 2026-10-14 |
| Licence read at source (repository `LICENSE`, no third-party text) | author | 2026-10-14 |
| Personal-data statement true (all rows invented) | author | 2026-10-14 |
| Derived bytes not in Git (`ml/.gitignore`; only the hand-written JSONL and this card are tracked) | author | 2026-10-14 |
| Native-speaker review of hi/bn/bn-Latn rows | **outstanding** | — |
| Card complete before first training run | author | 2026-10-14 |
