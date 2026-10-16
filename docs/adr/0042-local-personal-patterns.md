# ADR 0042 — Client-only personal patterns and small self-experiments

Date: 2026-10-16. Status: accepted.

## Context

Day 43 explores associations in decrypted personal history without creating a
server-side knowledge graph. This is wellbeing self-help, not clinical inference.
Builds on [Day 16 statistics](../../backend/app/services/mood/insights.py),
[ADR 0040](0040-in-browser-private-ai.md) and [ADR 0022](0022-privacy-controls-and-data-export.md).

## Decisions

- Pure TypeScript computes day-level mood comparisons. Multiple check-ins become
  one daily mean; tags are unioned. Missing days are never imputed. Untagged means
  unrecorded, not absence. Existing journal/exercise timestamps use device-local
  dates; mood uses its recorded local date (travel/timezone changes are a limitation).
- Port Day 16 **methods**, not Python's random byte stream: n>=5 per group,
  2,000 two-sided permutation draws with plus-one correction, 2,000 percentile
  bootstrap draws and 95% intervals, Cohen's d (null for zero spread), minimum
  absolute difference 0.4, Bonferroni 0.05/eligible comparisons, interval excludes
  zero. Independent stable seeded streams in JS. Mean intervals and differences
  are pointwise, not simultaneous. Data are observational and days may be dependent;
  neither p-values nor bootstraps remove confounding or serial dependence.
- Force-directed spring/repulsion layout settles once with no animation. Native
  buttons support arrows/Home/End, Tab and activation; an always-present text list
  offers identical selections. Numbers are not encoded by colour alone.
- Browser lexical TF-IDF/cosine clustering is the deliberately lightweight topic
  model. No external runtime, downloads, symptom dictionary, person/place inference,
  embeddings or LLM. Generic numbered themes only; explicit user naming, merging,
  deletion and date-based entry review. Topics are editable explorations, not labels
  of a person. Same-vocabulary clusters are imperfect, especially across languages.
- Only **explicit standard-mode reads** access existing mood, journal and exercise
  endpoints. The source module cannot receive derived state. Private Mode blocks
  that path; validated local JSON and all computation still work without any API.
  No graph/theme/experiment writes, telemetry, analytics or backend schema added.
  Bounded imports reject rather than silently truncate (365 days, 60 tags,
  20 themes, 2 MB input; existing mood read refuses a full 500-entry page).
- All working state is component memory, not React Query or browser persistence.
  Leaving the route clears it. Explicit unencrypted JSON export includes graph,
  day-level mood/tags, themes/membership and experiment, **never journal text**.
  Local file import resumes a two-week experiment. User must save after changes.
  Delete clears this page, cancels application of pending loads, resets the file
  input; it does not delete source records, other tabs or downloaded files. No
  promise of JS heap zeroisation. User copy states these boundaries.
- Seven of 14 dates assigned by Fisher-Yates with WebCrypto random draws; seeded
  injectable RNG for tests. Analyse assignment rather than reported adherence;
  require all 14 moods (no missing-data bias disguised as a result). Simple
  two-sided permutation test + bootstrap uncertainty, one planned comparison.
  No medical claims, extrapolation, repeated-testing guarantee or causal wording.

## Consequences / limits

No extra dependencies or server persistence. Graph route is lazy and excluded
from install-time precache to retain the existing budget; first use requires the
page assets (subsequent offline availability depends on the browser cache).
English-only Day 43 copy pending reviewed translations. No automatic experiment
persistence or reminders. Plaintext exports need deliberate handling. Serial
correlation, confounding, selective tagging, expectancy, travel and missingness
limit inference. Lexical themes are not semantic embeddings and are bounded to
mood-observed journal days. These limitations are disclosed, not hidden as model
capabilities. Real-data usability and assistive-technology checks remain manual.
