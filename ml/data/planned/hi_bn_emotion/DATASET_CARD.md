# Dataset card — Manovia hi/bn emotion set

> **Status: `planned` — this dataset does not exist yet.** The card is written
> first on purpose: it is what the plan will be judged against, and the fields
> that are still empty are the work (Day 32+). Fill every `plan` cell from the
> plan itself; a field that is genuinely not applicable says **n/a** with a
> reason.

| | |
| --- | --- |
| Card version | `0.1` (2026-10-13, draft) |
| Status | `planned` |
| Catalogue entry | `ml/data/CATALOGUE.md` § 14 |
| Owner / authors | Manovia maintainer (`AM`) + two volunteered native reviewers per language |
| Licence | MIT (repository licence, `LICENSE`) — authored in-repo |
| Licence evidence | `./LICENSE` (read 2026-10-13; own work, no third-party rights involved) |
| Last verified | 2026-10-13 |

## 1. What it is

A small, purpose-written Hindi, Hinglish and Bengali emotion set using Manovia's
own nine labels. It exists because **no openly licensed emotion corpus in these
languages could be used in the product** (see the catalogue's § "Hindi and
Bengali emotion data"). It is a review corpus for the label scheme and for the
hi/bn plumbing: it is too small to be a training corpus on its own, and that is
its intended scope.

## 2. Provenance

| | |
| --- | --- |
| Origin | Written (not scraped, not translated) by the project author and volunteer native speakers |
| Collection date | plan: first batch 2026-10-14 … review complete before any run |
| Collection method | Free-form authoring from invented everyday situations, one row per situation, committed as JSONL through DVC |
| Platform terms | n/a — no platform involved |
| Personal data | **no** — invented situations only. Written rule for every contributor: do not transcribe, adapt or paraphrase anyone's real messages, including your own |
| Consent basis | Contributors are authors of their own sentences and have read this card; they are credited by initials only if they choose to be |
| Redistribution | yes — MIT, own work (the one ML dataset that *may* be committed; it still will not be, to keep the rule "no dataset bytes in Git" absolute) |

## 3. Contents

| | |
| --- | --- |
| Languages | `hi` (Devanagari), `hi-Latn` (romanised Hinglish), `bn` (Bengali script) |
| Size | plan: ≥ 25 rows per (label × variant) ≈ 675 rows; dev split ≥ 8 per cell |
| Label scheme | Manovia's nine: joy, sadness, anger, fear, anxiety, shame, loneliness, calm, neutral (definitions: `backend/app/content/i18n/`) |
| Label quality | plan: two independent native reviewers per language; a row is accepted only on agreement, disagreements to a third reviewer and recorded either way |
| Fields | `id: str`, `text: str`, `lang: hi \| hi-Latn \| bn`, `label: str`, `register: first_person \| quoted_other \| indirect_somatic`, `author: str (initials)`, `review: {hi, bn} record` |
| Hashes | plan: `dvc.lock` + the split manifest (`manovia_ml.data.toy`-style manifest for this set) |

## 4. Known biases

- **Authored, not observed.** Registers and situations are the writers' choices;
  real users will write about things this set never mentions.
- **Reviewer overlap.** Two reviewers per language is a floor, not a
  distribution: agreement between two people is not the same as a validated
  label scheme.
- **Missing code-mixing and scripts.** No `bn-Latn`, no Tamil/Urdu, no
  voice-transcribed text; code-mixing will be under-represented relative to real
  Hinglish.
- **Calm/sadness overlap** is the known hard pair in the English eval set too;
  the set must include it deliberately rather than avoid it.

## 5. Fitness for Manovia

| use | allowed? | notes |
| --- | --- | --- |
| Training a shipped model | `with conditions` | only after native review completes and the card says `frozen` |
| Evaluation / benchmark | `yes` | its main purpose: label-scheme and plumbing review |
| Research comparison only | `yes` | |
| Committed to Git | `no` | hash + stats + this card are committed; rows go through DVC |

## 6. Handling

- Storage: DVC-tracked under `ml/data/raw/hi_bn_emotion/`, never committed.
- Retention: kept while the label scheme is in use; re-reviewed annually.
- Deletion: `dvc remove` + delete the cache entry (recorded here when it happens).
- Access: public-once-frozen (own work), but no credentials are ever stored in
  the repository.

## 7. Splits

| split | rows | how chosen | may it be changed? |
| --- | --- | --- | --- |
| train | plan ≥ 15 per cell | seeded stratification by (label × variant), seed recorded in the split manifest | yes, with a new hash and a re-recorded baseline |
| eval | plan ≥ 8 per cell | same split, disjoint | yes, loud |
| test | plan ≥ 2 per cell | held back at authoring time | **frozen** — hash pinned before the first run |

## 8. Review record

| reviewer | role | scope | date | outcome |
| --- | --- | --- | --- | --- |
| `<initials>` | native speaker (hi) | all `hi` + `hi-Latn` rows | | |
| `<initials>` | native speaker (bn) | all `bn` rows | | |

## 9. Sign-off

| check | who | date |
| --- | --- | --- |
| Catalogue entry complete and verified | AM | 2026-10-13 |
| Licence read at source (own work, MIT) | AM | 2026-10-13 |
| Personal-data statement true | *(pending — first batch not written)* | |
| No rows in Git (`git ls-files` checked) | *(pending)* | |
| Card complete before first training run | *(pending)* | |
