# Model card — Private-mode on-device

| | |
| --- | --- |
| Card version | 1.0 (2026-10-20) |
| Status | experimental — no model bytes pinned |
| Manifest | `frontend/public/private/model-manifest.json` (unpinned in repo) |
| Owner | Manovia maintainers |

## 1. What it is

Compiled safety bundle (191 KB, 215 rules, 66 output rules, 3 locales) + optional classifiers (distilled) + optional small LLM via WebLLM/transformers.js. All on-device; no text leaves device.

## 2. Evaluation

Parity 500/500 (0 mismatches) between Python and browser rules engine. Offline e2e 4/4 with 0 network attempts. No real inference measured (WebLLM adapter stub only).

## 3. Limits

- No model pinned: answers are templates everywhere until operator runs PIN.
- WebLLM cache unverified (managed_by_runtime).
- Crisis replies show international helplines (no region in private mode).

## 4. Kill switch

`private_mode_models=false` → templates only; rules still assess risk. Delete downloaded models button available.
