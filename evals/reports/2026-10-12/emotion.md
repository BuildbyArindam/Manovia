# Emotion analyzer evaluation — 2026-10-12

- Dataset: `evals/datasets/emotion_cases.jsonl` (162 hand-labelled synthetic check-ins across `en`, `hi`, `bn` and all 9 emotions).
- Pipeline under test: `AnalyzerChain(KeywordFallbackAnalyzer(), [SentimentAnalyzer()])` (the deterministic offline chain used when the optional HuggingFace transformer is absent or declines non-English text).
- Bootstrap: B=2000 resamples, 95% percentile CIs (seed=20261012).
- Predictor source usage on this set: `keyword`=162, `sentiment`=0.

## Headline metrics (9-class emotion taxonomy)

| analyzer | accuracy | macro-precision | macro-recall | macro-F1 | macro-F1 95% CI | accuracy 95% CI |
| --- | --- | --- | --- | --- | --- | --- |
| **keyword + sentiment chain (shipped offline)** | **0.765** | **0.902** | **0.765** | **0.804** | [0.739, 0.853] | [0.698, 0.827] |
| keyword lexicon only | 0.765 | 0.902 | 0.765 | 0.804 | [0.744, 0.850] | [0.698, 0.827] |
| sentiment polarity only | 0.210 | 0.119 | 0.210 | 0.125 | [0.086, 0.165] | [0.148, 0.272] |

## CI gate status

Overall CI gate verdict: **PASS** (configured in `evals/thresholds.yaml`)

| check | threshold | observed | verdict | notes |
| --- | --- | --- | --- | --- |
| Overall macro-F1 (9-class emotion chain) | `>= 0.550` | `0.804` | **PASS** | Evaluated across all 162 cases (in-domain + domain-shift) |
| Per-language macro-F1 floor (en, hi, bn) | `>= 0.450` | `0.774 (bn)` | **PASS** | en=0.802, hi=0.833, bn=0.774 |

## Per-emotion metrics (`keyword + sentiment chain`)

| emotion | support | precision | recall | F1 | F1 95% CI |
| --- | --- | --- | --- | --- | --- |
| `joy` | 18 | 0.875 | 0.778 | **0.824** | [0.667, 0.944] |
| `sadness` | 18 | 1.000 | 0.778 | **0.875** | [0.714, 0.973] |
| `anger` | 18 | 1.000 | 0.722 | **0.839** | [0.667, 0.963] |
| `fear` | 18 | 1.000 | 0.778 | **0.875** | [0.720, 0.973] |
| `anxiety` | 18 | 0.933 | 0.778 | **0.848** | [0.692, 0.966] |
| `shame` | 18 | 1.000 | 0.667 | **0.800** | [0.600, 0.933] |
| `loneliness` | 18 | 1.000 | 0.778 | **0.875** | [0.727, 0.973] |
| `calm` | 18 | 1.000 | 0.722 | **0.839** | [0.667, 0.957] |
| `neutral` | 18 | 0.314 | 0.889 | **0.464** | [0.295, 0.603] |

## Confusion matrix (9×9, `keyword + sentiment chain`)

Rows are ground-truth labels; columns are predicted primary emotions.

| true \ pred | joy | sadness | anger | fear | anxiety | shame | loneliness | calm | neutral | total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **joy** | **14** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 18 |
| **sadness** | 0 | **14** | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 18 |
| **anger** | 1 | 0 | **13** | 0 | 0 | 0 | 0 | 0 | 4 | 18 |
| **fear** | 0 | 0 | 0 | **14** | 0 | 0 | 0 | 0 | 4 | 18 |
| **anxiety** | 0 | 0 | 0 | 0 | **14** | 0 | 0 | 0 | 4 | 18 |
| **shame** | 0 | 0 | 0 | 0 | 0 | **12** | 0 | 0 | 6 | 18 |
| **loneliness** | 0 | 0 | 0 | 0 | 0 | 0 | **14** | 0 | 4 | 18 |
| **calm** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **13** | 5 | 18 |
| **neutral** | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | **16** | 18 |

## Per-language performance

| language | cases | accuracy | macro-precision | macro-recall | macro-F1 | macro-F1 95% CI |
| --- | --- | --- | --- | --- | --- | --- |
| English (`en`) | 72 | 0.764 | 0.896 | 0.764 | **0.802** | [0.694, 0.873] |
| Hindi (Devanagari + Hinglish) (`hi`) | 45 | 0.800 | 0.904 | 0.800 | **0.833** | [0.690, 0.916] |
| Bengali (Bengali + Banglish) (`bn`) | 45 | 0.733 | 0.922 | 0.733 | **0.774** | [0.618, 0.868] |

## Impact of domain shift (`in_domain` vs `domain_shift`)

| regime | cases | accuracy | macro-precision | macro-recall | macro-F1 | macro-F1 95% CI |
| --- | --- | --- | --- | --- | --- | --- |
| In-domain (explicit lexical emotion cues) | 123 | 0.992 | 0.993 | 0.992 | **0.992** | [0.972, 1.000] |
| Domain-shift (somatic, implicit, idiomatic, or unmapped script) | 39 | 0.051 | 0.006 | 0.056 | **0.011** | [0.000, 0.026] |

Observed domain-shift penalty: macro-F1 drops by **0.981** (from **0.992** on explicit lexical check-ins to **0.011** on somatic, idiomatic, sarcastic, or unmapped-script check-ins).

## Documented limitations (why emotion scores must never drive safety)

1. **Domain shift (Reddit GoEmotions -> Indian student/worker wellbeing check-ins)**:
   - Even when the optional English `SamLowe/roberta-base-go_emotions` transformer is loaded, its training distribution is short US/UK Reddit comments. Indian users frequently describe distress somatically (*"my shoulders are up by my ears"*, *"हाथ-पैर ठंडे पड़ गए"*, *"বুকটা ধড়াস করে উঠল"*), behaviourally (*"stared at my dinner plate untouched"*), or through family/exam framing rather than explicit affect adjectives.
2. **Taxonomy gaps between GoEmotions (27 labels) and Manovia's 9-emotion schema**:
   - `loneliness` has **no corresponding label** in GoEmotions (`LABEL_MAP` in `backend/app/services/nlp/emotion.py` maps 0 of 27 heads to `loneliness`); it can only be surfaced when `KeywordFallbackAnalyzer` matches a token like `lonely`, `akela`, or `একা`.
3. **Cross-lingual & script coverage gaps**:
   - The HuggingFace checkpoint is English-only and declines `hi` and `bn`, so Hindi and Bengali check-ins always route through `KeywordFallbackAnalyzer` -> `SentimentAnalyzer`. Several emotion categories have sparse native-script coverage (for example, `shame` has romanised Hindi words `sharm`/`sharminda` and Devanagari `शर्म`, but no Bengali-script tokens for `লজ্জা` or `অপরাধবোধ`).
4. **Polarity fallback collapses 9 classes into 4**:
   - When `KeywordFallbackAnalyzer` finds no keyword hit, `SentimentAnalyzer` maps scalar polarity solely onto `{"joy", "calm", "neutral", "sadness"}`. Any implicit `anger`, `fear`, `anxiety`, `shame`, or `loneliness` utterance that misses the keyword lexicon is therefore misclassified as `neutral` (if no polarity words match) or `sadness` (if negative polarity words match).
5. **Contextual negation and academic/figurative mentions**:
   - Bag-of-words keyword matching triggers on emotion words used inside book titles or academic notes (e.g., *"reading a chapter on the Great Depression"* -> `sadness`; *"meeting about the frightening budget numbers"* -> `fear`).
6. **Architectural consequence**:
   - Because of these limitations, emotion labels are used **only** as a soft tone hint for the LLM prompt (`build_prompt`) and for opt-in mood-trend visualisations. They **never** gate crisis escalation (`SafetyTriageService` runs independently on every turn).

## Representative failure modes (38 misclassified of 162)

- `emo-en-007` (`en`, `domain_shift`): truth=`joy` -> pred=`neutral` (source=`keyword`, conf=0.00) — "For the first time in months, I sang along to the radio while making tea." *(note: domain_shift: behavioural sign of returning joy without emotion words)*
- `emo-en-008` (`en`, `domain_shift`): truth=`joy` -> pred=`neutral` (source=`keyword`, conf=0.00) — "My code finally compiled on the first run and I did a little dance at my desk." *(note: domain_shift: situational elation without explicit lexicon hit)*
- `emo-hi-005` (`hi`, `domain_shift`): truth=`joy` -> pred=`neutral` (source=`keyword`, conf=0.00) — "माँ के हाथ की चाय पीते ही चेहरे पर अपने आप मुस्कान आ गई।" *(note: domain_shift: Devanagari मुस्कान not in tiny keyword lexicon)*
- `emo-bn-005` (`bn`, `domain_shift`): truth=`joy` -> pred=`neutral` (source=`keyword`, conf=0.00) — "ছাদে দাঁড়িয়ে গানের সুর গুনগুন করছি, আজ মনটা বেশ ফুরফুরে।" *(note: domain_shift: colloquial Bengali ফুরফুরে outside lexicon)*
- `emo-en-015` (`en`, `domain_shift`): truth=`sadness` -> pred=`neutral` (source=`keyword`, conf=0.00) — "I stared at my dinner plate for twenty minutes and put it back in the fridge untouched." *(note: domain_shift: behavioural flat affect / appetite loss without emotion words)*
- `emo-en-016` (`en`, `domain_shift`): truth=`sadness` -> pred=`neutral` (source=`keyword`, conf=0.00) — "Even my favourite songs sound grey and distant this week." *(note: domain_shift: anhedonia metaphor)*
- `emo-hi-010` (`hi`, `domain_shift`): truth=`sadness` -> pred=`neutral` (source=`keyword`, conf=0.00) — "कमरे की बत्ती बुझाकर घंटों छत को देखता रहा, कुछ करने की इच्छा ही नहीं हुई।" *(note: domain_shift: Devanagari behavioural withdrawal without lexicon hit)*
- `emo-bn-010` (`bn`, `domain_shift`): truth=`sadness` -> pred=`neutral` (source=`keyword`, conf=0.00) — "জানলার ধারে চুপচাপ বসে বৃষ্টি দেখছি, বুকের ভেতরটা কেমন ফাঁকা লাগছে।" *(note: domain_shift: Bengali emptiness metaphor ফাঁকা)*
- `emo-en-023` (`en`, `domain_shift`): truth=`anger` -> pred=`neutral` (source=`keyword`, conf=0.00) — "My jaw is clenched so tight from replaying that conversation that my teeth ache." *(note: domain_shift: somatic anger without lexicon words)*
- `emo-en-024` (`en`, `domain_shift`): truth=`anger` -> pred=`joy` (source=`keyword`, conf=0.60) — "Oh wonderful, another last-minute rewrite dumped on my desk at 6 p.m." *(note: domain_shift: sarcastic frustration; surface word 'wonderful' misleads lexicons)*
- `emo-hi-015` (`hi`, `domain_shift`): truth=`anger` -> pred=`neutral` (source=`keyword`, conf=0.00) — "मेरी सारी मेहनत पर पानी फेर दिया और ऊपर से मुझे ही सुना रहे हैं।" *(note: domain_shift: Hindi idiom for indignation without गुस्सा)*
- `emo-bn-014` (`bn`, `domain_shift`): truth=`anger` -> pred=`neutral` (source=`keyword`, conf=0.00) — "আমার কাজটা নিজের নামে চালিয়ে দিল, মাথাটা গরম হয়ে যাচ্ছে।" *(note: domain_shift: Bengali idiom মাথা গরম (angry) not in lexicon)*
