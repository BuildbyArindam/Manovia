# Dataset card — Drift aggregates (Day 48, privacy-preserving)

| | |
| --- | --- |
| Card version | 1.0 (2026-10-20) |
| Status | frozen (format) |
| Catalogue entry | `ml/data/CATALOGUE.md` § 19 (new) |
| Owner / authors | Manovia maintainers |
| Licence | n/a — not a external dataset; internal aggregates |
| Licence evidence | n/a |
| Last verified | 2026-10-20 |

## 1. What it is

Weekly aggregate statistics for drift monitoring. Contains no raw text, no embeddings, no identifiers — only histograms (counts per bin), rates, and a centroid mean vector computed on-the-fly. Used to detect shifts in input length, language mix, embedding centroid, confidence, safety-level rates, abstention and guard block rates.

## 2. Provenance

| | |
| --- | --- |
| Origin | derived aggregates from production inference (synthetic in this sandbox) |
| Collection date | ongoing; this sandbox uses demo weeks 2026-W40 (baseline) and 2026-W42 (current) |
| Collection method | `backend/app/services/drift/monitor.py` — `record()` called per request, binning on the fly |
| Personal data | no — aggregates only, k≥ 800 per window in demo |
| Consent basis | n/a |
| Redistribution | Report JSON/MD in `ml/reports/drift/`; snapshots never committed with real traffic |

## 3. Contents

| | |
| --- | --- |
| Languages | categorical histogram over {en, hi, hi-Latn, bn, other} |
| Size | per-window: 6 length bins, 5 language bins, 5 confidence bins, 5 safety bins, 1 centroid vector (4-dim demo), rates |
| Label scheme | n/a |
| Label quality | n/a |
| Fields | `window`, `total_inputs`, `length_hist`, `language_hist`, `confidence_hist`, `safety_hist`, `embedding_centroid`, `abstention_rate`, `guard_block_rate` |
| Hashes | reports are artefacts with gitignored real-window data; demo report is committed example |

## 4. Known biases

- Aggregates collapse detail; a drift inside a bin is invisible.
- Language detection is heuristic; hi-Latn vs en is noisy.
- Embedding centroid is mean of hashed dims in demo; real centroid would be over real embeddings.
- Weekly window choice (Monday-Sunday) may hide intra-week patterns.

## 5. Fitness

| use | allowed? | notes |
| --- | --- | --- |
| Training | no |  |
| Evaluation | yes | Weekly check; PSI/KS thresholds 0.10/0.25, 0.15/0.30 |
| Committed | yes | Demo report only; real reports go to external store |

## 6. Handling

- Storage: process memory + `ml/reports/drift/weekly_*.json` (demo), real store would be durable TSDB.
- Retention: aggregates kept 90 days; no raw data ever stored here.
- Access: ops + safety lead only.

## 7. Splits

Not applicable — sliding window comparison (baseline vs current).

## 8. Review

- Privacy review: confirmed no text/ids in aggregates; on-the-fly centroid discards embeddings after mean update.
