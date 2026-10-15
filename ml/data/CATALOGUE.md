# Manovia data catalogue

Every dataset Manovia has **used**, is **considering**, has **planned**, or has
**rejected** — with the licence, what it may be used for, and what it would do to
the people whose words are in it.

This file is the gate in front of `ml/` (AGENTS.md, ML ground rule 4). ML
ground rule 3 says no dataset bytes are committed; this catalogue is how a run
that *does* use a dataset stays checkable without the bytes.

## How to read this

Each entry is a fenced YAML block under `catalogue:` — that block is the record,
and `ml/tests/test_catalogue.py` parses it, so a claim here is a claim the CI
can check. The summary table below the policy section is **generated** from those
blocks (`make ml-catalogue`) and a test fails if it drifts.

Verdicts:

| verdict | meaning |
| --- | --- |
| **USE** | licence and provenance verified; may ship in a product feature |
| **USE WITH CONDITIONS** | may be used, with the stated conditions met every time (attribution, no redistribution, scrubbing, a card) |
| **RESEARCH ONLY** | may be used for a comparison we publish as research; never trained into a shipped model and never used to make a product decision |
| **DO NOT USE** | restrictive, unverifiable or unknown licence, or the personal-data risk is unacceptable — do not download, train on, or evaluate against it |

Rules that are enforced by tests, not by good intentions:

1. Anything with an unclear or restrictive licence is **DO NOT USE**.
2. A licence claim on a mirror (Kaggle, a Hugging Face re-upload) is **not**
   evidence about the original release. Where a mirror is the only source, the
   entry is DO NOT USE until the original is found.
3. A dataset containing personal data is never plainly **USE**.
4. A dataset marked DO NOT USE (or RESEARCH ONLY) may not be named in any config
   under `ml/configs/`.
5. Licence verification is dated. Older than 180 days is a failure, because
   re-hosting and relicensing happen.

`verified_on` in each block means: *on that date, a human read the licence at
`licence_evidence`.* Verification was done on 2026-10-13 by reading the pages
listed in § Verification log.

## Summary

<!-- catalogue-table:start -->
| # | Dataset | Languages | Licence | Allowed use | Personal data | Verdict | Verified |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | GoEmotions | en | Apache-2.0 | commercial | yes | USE WITH CONDITIONS | 2026-10-13 |
| 2 | EmoNoBa | bn | CC BY 4.0 | commercial | yes | USE WITH CONDITIONS | 2026-10-13 |
| 3 | emoHind-58K | hi | CC BY 4.0 (as stated by the Kaggle uploader; original release not found) | none | yes | DO NOT USE | 2026-10-13 |
| 4 | IndicSentiment / IndicGLUE sentiment | hi, bn, as, gu, kn, ml, mr, or, pa, ta, te | CC BY-NC 4.0 | research-only | yes | RESEARCH ONLY | 2026-10-13 |
| 5 | EmoInHindi | hi | CC BY-NC-ND 4.0 | research-only | yes | RESEARCH ONLY | 2026-10-13 |
| 6 | SentiMix Hinglish (SemEval-2020 Task 9) | hi-Latn, en | paper CC BY 4.0; corpus released via CodaLab under task terms (not read in full) | research-only | yes | RESEARCH ONLY | 2026-10-13 |
| 7 | Bengali & Banglish | bn, bn-Latn | CC BY-NC 4.0 | research-only | yes | RESEARCH ONLY | 2026-10-13 |
| 8 | EmpatheticDialogues | en | CC BY-NC 4.0 | research-only | yes | DO NOT USE | 2026-10-13 |
| 9 | ESConv (Emotional Support Conversation) | en | academic research use only (repository statement; no OSI licence) | none | yes | DO NOT USE | 2026-10-13 |
| 10 | MentalChat16K | en | MIT per the Hugging Face card; the included real transcripts come from the PISCES clinical trial | none | yes | DO NOT USE | 2026-10-13 |
| 11 | eRisk / CLEF depression corpora (DepreSym) | en | research use under a signed user agreement (data transfer agreement), no open licence | none | yes | DO NOT USE | 2026-10-13 |
| 12 | Dreaddit / SMHD (self-reported mental-health diagnoses) | en | none published that we could find; distributed by the authors on request | none | yes | DO NOT USE | 2026-10-13 |
| 13 | Manovia fixtures (evals/datasets/*, ml/data/toy/*) | en, hi, hi-Latn, bn | MIT (repository licence, LICENSE); authored in this repository | commercial | no | USE | 2026-10-13 |
| 14 | Manovia hi/bn emotion set (planned, not yet written) | hi, hi-Latn, bn | MIT (repository licence, LICENSE); authored for Manovia | commercial | no | USE WITH CONDITIONS | 2026-10-13 |
| 15 | Manovia synthetic safety corpus (expanded from the Day-9 seeds) | en, hi, hi-Latn, bn | MIT (repository licence, LICENSE); authored in this repository | commercial | no | USE | 2026-10-13 |
| 16 | Manovia retrieval eval set (hand-written + template-paraphrased) | en, hi, hi-Latn, bn, bn-Latn | MIT (repository licence, LICENSE); authored in this repository | commercial | no | USE | 2026-10-14 |
<!-- catalogue-table:end -->

## Entries

### 1. GoEmotions

```yaml
catalogue:
  name: GoEmotions
  source: https://github.com/google-research/google-research/tree/master/goemotions
  licence: Apache-2.0
  licence_evidence: https://huggingface.co/datasets/google-research-datasets/go_emotions/blob/main/README.md
  allowed_use: commercial
  languages: [en]
  size: "58,009 Reddit comments (43,410 train / 5,426 validation / 5,427 test in the filtered split)"
  label_scheme: "27 emotion classes + neutral; multi-label (up to 5 per row)"
  known_biases: >
    Reddit comments: US-centric, young, English-speaking, heavily sarcastic and
    ironic, with subreddit-specific register. Multi-label rows collapse badly
    into a single-label scheme. No loneliness class exists, and the rater
    agreement filtered split removes exactly the ambiguous examples that look
    most like real user text.
  personal_data: true
  personal_data_notes: >
    Public Reddit comments. Usernames are removed in the released files, but the
    text can contain self-disclosure, relationships, locations and health
    details, and a comment can be re-found by search. Treat every row as
    potentially identifying: never redistribute rows, never quote a row in a
    report, cache the corpus outside Git (DVC) and delete it when the experiment
    ends.
  status: candidate
  verdict: USE WITH CONDITIONS
  verdict_reason: >
    Apache-2.0 permits commercial use with attribution, and it is the standard
    English fine-grained emotion benchmark, so it gives Manovia a comparison
    point. Conditions: attribution + licence file, no redistribution of rows,
    treat as personal data, and say in every report that it is a domain shift
    from a wellbeing companion.
  verified_on: 2026-10-13
```

**What it is for:** the first real English training run
(`ml/configs/colab_goemotions_logreg.yaml`, `ml/notebooks/`), and a baseline
number for the emotion analyser that is comparable to published work.

**What it cannot tell us:** whether Manovia's nine-emotion scheme is right, how
people write to a companion rather than to a subreddit, or anything at all about
Hindi, Bengali or Hinglish (see § Hindi and Bengali emotion data).

### 2. EmoNoBa

```yaml
catalogue:
  name: EmoNoBa
  source: https://aclanthology.org/2022.aacl-short.17/
  licence: CC BY 4.0
  licence_evidence: https://www.kaggle.com/datasets/saifsust/emonoba
  allowed_use: commercial
  languages: [bn]
  size: "~15k noisy Bangla social-media comments (the published test split alone is 2,272 rows)"
  label_scheme: "six emotions (anger, fear, joy, love, sadness, surprise), multi-label 0/1 columns"
  known_biases: >
    Social-media comment text: typos, transliteration, slang and profanity, which
    is closer to how people write than a clean review corpus is. Collected from
    comment sections, so it skews young, male and argumentative. No anxiety,
    shame or loneliness class; "love" absorbs a lot of what Manovia would call
    calm or joy.
  personal_data: true
  personal_data_notes: >
    Public comment text with usernames removed. Comment sections are
    re-identifiable by search, and the corpus contains abuse and profanity;
    never redistribute rows and never quote one.
  status: candidate
  verdict: USE WITH CONDITIONS
  verdict_reason: >
    CC BY 4.0 with attribution, and it is the best openly licensed Bangla
    emotion corpus found. Conditions: attribution to the AACL-IJCNLP 2022 paper
    and authors, no redistribution, map only onto overlapping labels (dropping
    "love"/"surprise" rather than guessing), and report the register bias.
  verified_on: 2026-10-13
```

**The gap it leaves:** Manovia's `anxiety`, `shame` and `loneliness` classes do
not exist in EmoNoBa, so it cannot train or validate them. That is one reason the
hand-written set in § Hindi and Bengali emotion data is needed *in addition*, not
instead.

### 3. emoHind-58K (Hindi)

```yaml
catalogue:
  name: emoHind-58K
  source: https://www.kaggle.com/datasets/debadityashome/emohi58k-finegrained-hindi-emotion-datatext
  licence: "CC BY 4.0 (as stated by the Kaggle uploader; original release not found)"
  licence_evidence: https://www.kaggle.com/datasets/debadityashome/emohi58k-finegrained-hindi-emotion-datatext
  allowed_use: none
  languages: [hi]
  size: "~58k rows claimed by the uploader (not independently verified)"
  label_scheme: "multi-label emotion classes over Hindi text; the uploader's own scheme, no paper identified"
  known_biases: >
    Unknown: no paper, no documentation of collection or annotation process, no
    statement of inter-annotator agreement. The upload provides a large Hindi
    emotion corpus and nothing else.
  personal_data: true
  personal_data_notes: >
    Wherever the rows came from (X/Twitter is the usual source for corpora of
    this size), they are user-written text with the same re-identification risk
    as the other social corpora, plus health-adjacent content.
  status: rejected
  verdict: DO NOT USE
  verdict_reason: >
    The only licence statement is a third-party upload. The original release
    (paper, author, repository) could not be identified on 2026-10-13, so the
    licence cannot be traced to whoever holds the rights — and an untraceable
    licence is exactly the case the rules send back. Revisit only if the original
    release is found and its licence read there.
  verified_on: 2026-10-13
```

### 4. IndicSentiment (AI4Bharat) and IndicGLUE sentiment

```yaml
catalogue:
  name: IndicSentiment / IndicGLUE sentiment
  source: https://indicnlp.ai4bharat.org/indic-glue/
  licence: CC BY-NC 4.0
  licence_evidence: https://indicnlp.ai4bharat.org/indic-glue/
  allowed_use: research-only
  languages: [hi, bn, as, gu, kn, ml, mr, or, pa, ta, te]
  size: "≈886k rows across the IndicGLUE tasks; IndicSentiment is review text translated into 11 Indic languages"
  label_scheme: "sentiment polarity (positive / negative / neutral), not emotion"
  known_biases: >
    Machine-translated product reviews: formal register, translated idiom, and no
    sadness/fear/anxiety signal at all. Polarity is not emotion, so mapping it
    onto Manovia's labels would invent semantics the annotators never had.
  personal_data: true
  personal_data_notes: >
    Review text about products and services; contains opinions about named
    organisations and, occasionally, people. Not health data, but not
    attributable either.
  status: rejected
  verdict: RESEARCH ONLY
  verdict_reason: >
    The IndicNLP Suite licence is CC BY-NC 4.0: non-commercial, so it cannot go
    into a product that might ever be monetised, and its sentiment labels do not
    map onto Manovia's emotions anyway. Usable only in a published research
    comparison, never in a shipped model.
  verified_on: 2026-10-13
```

### 5. EmoInHindi

```yaml
catalogue:
  name: EmoInHindi
  source: https://arxiv.org/abs/2205.13908
  licence: CC BY-NC-ND 4.0
  licence_evidence: https://www.researchgate.net/publication/360936237_EmoInHindi_A_Multi-label_Emotion_and_Intensity_Annotated_Dataset_in_Hindi_for_Emotion_Recognition_in_Dialogues
  allowed_use: research-only
  languages: [hi]
  size: "1,814 dialogues, 44,247 utterances (Wizard-of-Oz, mental-health and legal counselling settings)"
  label_scheme: "multi-label emotion + intensity per utterance"
  known_biases: >
    Wizard-of-Oz dialogues: the "user" is a participant following a fictional
    scenario, the counsellor knows they are in a study, and the mental-health
    subset is about crime victims — a population and a register that would not
    transfer to everyday use.
  personal_data: true
  personal_data_notes: >
    Simulated counselling conversations; the paper states PII was filtered, but
    the settings are health/legal and the participants are real people whose
    utterances may still describe real events.
  status: rejected
  verdict: RESEARCH ONLY
  verdict_reason: >
    NoDerivatives and NonCommercial: it may not be used to train or adapt a
    model, and it may not ship. The most useful part of it is the *method*
    (multi-label + intensity on Hindi dialogue), not the rows.
  verified_on: 2026-10-13
```

### 6. SemEval-2020 Task 9 (SentiMix, Hinglish)

```yaml
catalogue:
  name: SentiMix Hinglish (SemEval-2020 Task 9)
  source: https://ritual-uh.github.io/sentimix2020/
  licence: "paper CC BY 4.0; corpus released via CodaLab under task terms (not read in full)"
  licence_evidence: https://aclanthology.org/2020.semeval-1.99/
  allowed_use: research-only
  languages: [hi-Latn, en]
  size: "≈20k Hinglish tweets with word-level language tags and sentence-level polarity"
  label_scheme: "polarity (positive / negative / neutral) plus language tags, not emotion"
  known_biases: >
    Twitter register, hashtags, handles, spelling chaos, and polarity rather than
    emotion. Romanised Hinglish of that era differs from what people type today.
  personal_data: true
  personal_data_notes: >
    Tweets by identifiable users; the released data includes handles/links in
    several distributions. The platform's terms restrict redistribution and
    commercial use independently of the paper licence.
  status: rejected
  verdict: RESEARCH ONLY
  verdict_reason: >
    Two separate licences apply (the paper's CC BY 4.0 and the platform's terms
    for the text), and only one of them was read. Until the task's data terms are
    read in full and the platform question is answered, this is research-only —
    and in practice it is also the wrong label scheme (polarity, not emotion).
  verified_on: 2026-10-13
```

### 7. "Bengali & Banglish" (Faisal et al., Data in Brief 2024)

```yaml
catalogue:
  name: Bengali & Banglish
  source: https://doi.org/10.1016/j.dib.2024.110760
  licence: CC BY-NC 4.0
  licence_evidence: https://www.researchgate.net/publication/382430701_Bengali_Banglish_A_Monolingual_Dataset_for_Emotion_Detection_in_Linguistically_Diverse_Contexts
  allowed_use: research-only
  languages: [bn, bn-Latn]
  size: ">80,000 samples across six emotional states, combining EmoNoBa, UBMEC and MONOVAB with newly scraped YouTube/X comments"
  label_scheme: "six emotional states (English-mapped emotions)"
  known_biases: >
    It re-uses EmoNoBa (already a comment-section corpus) and adds YouTube/X
    comments scraped through platform APIs — so the platform-terms question
    applies again, on top of the register bias.
  personal_data: true
  personal_data_notes: >
    User comments from two platforms; same re-identification risk as EmoNoBa,
    with a second platform's terms stacked on top.
  status: rejected
  verdict: RESEARCH ONLY
  verdict_reason: >
    NonCommercial licence, and the Romanised Bangla coverage it offers could not
    be separated from the rest for a commercial use even if we wanted it. Noted
    in the catalogue because it is the obvious "Banglish" candidate, and the
    answer is no for a product.
  verified_on: 2026-10-13
```

### 8. EmpatheticDialogues

```yaml
catalogue:
  name: EmpatheticDialogues
  source: https://github.com/facebookresearch/EmpatheticDialogues
  licence: CC BY-NC 4.0
  licence_evidence: https://parl.ai/docs/tasks.html
  allowed_use: research-only
  languages: [en]
  size: "≈25k (76,673 train / 12,030 validation / 10,943 test utterances) grounded emotional conversations"
  label_scheme: "32 emotion labels attached to the situation, plus dialogue turns"
  known_biases: >
    Crowdworkers role-playing empathy in a paid task, in English, in a
    text-only chat window. The "listener" turns are written to be judged as
    empathetic responses, which optimises for how empathy *sounds*.
  personal_data: true
  personal_data_notes: >
    The situations are invented for the task by crowdworkers, but people often
    adapt personal material; the released data is anonymised and usernames never
    existed.
  status: rejected
  verdict: DO NOT USE
  verdict_reason: >
    NonCommercial: it cannot be used in a shipped product or in training that may
    end up in one, and the Day-31 rules send restrictive licences to DO NOT USE
    outright. It is a good benchmark for research on empathy, not for Manovia's
    product path.
  verified_on: 2026-10-13
```

### 9. ESConv

```yaml
catalogue:
  name: ESConv (Emotional Support Conversation)
  source: https://github.com/thu-coai/Emotional-Support-Conversation
  licence: "academic research use only (repository statement; no OSI licence)"
  licence_evidence: https://github.com/thu-coai/Emotional-Support-Conversation
  allowed_use: none
  languages: [en]
  size: "1,300 conversations / ~31k utterances with support-strategy annotations"
  label_scheme: "eight support strategies plus a problem topic and a conversation-level emotion"
  known_biases: >
    Crowdworker dyads (seeker/supporter) following a task, with supporters told
    to use the strategy labels. Reads as a scripted counsellor, not a companion.
  personal_data: true
  personal_data_notes: >
    The authors state PII was filtered, but the conversations are about real
    problems people brought to the task, including distress.
  status: rejected
  verdict: DO NOT USE
  verdict_reason: >
    "for academic research use only" with no defined terms is exactly the
    unclear-licence case: we cannot tell whether a research-only use extends to
    Manovia's research, let alone anything else. Not used.
  verified_on: 2026-10-13
```

### 10. MentalChat16K

```yaml
catalogue:
  name: MentalChat16K
  source: https://huggingface.co/datasets/ShenLab/MentalChat16K
  licence: "MIT per the Hugging Face card; the included real transcripts come from the PISCES clinical trial"
  licence_evidence: https://github.com/PennShenLab/MentalChat16K
  allowed_use: none
  languages: [en]
  # The generator model is deliberately described rather than named: the
  # repository forbids vendor model ids outside configuration, and a guard test
  # scans every text file to enforce it (backend/tests/llm/test_no_hardcoded_model.py).
  # The exact id is in the source paper and on the dataset card.
  size: "16,113 question–answer pairs: 6,338 paraphrased from 378 PISCES trial transcripts + 9,775 synthetic dialogues from a proprietary chat model"
  label_scheme: "no emotion labels — question/answer pairs about 33 mental-health topics"
  known_biases: >
    The real half is from caregivers of palliative/hospice patients — a specific,
    heavily burdened population, paraphrased by someone else, in a clinical
    setting. The synthetic half is role-play generated by a proprietary chat
    model — fluent, and not written by anyone who has felt the thing.
  personal_data: true
  personal_data_notes: >
    Clinical-trial transcripts: health data about identifiable-in-context
    people, paraphrased but not consented for Manovia's use.
  status: rejected
  verdict: DO NOT USE
  verdict_reason: >
    An MIT label on a Hub card cannot grant rights the uploader does not hold,
    and the real transcripts need the trial's own consent and ethics framework —
    which is not something a Kaggle/HF download can supply. Also the wrong shape
    for this work: no emotion labels.
  verified_on: 2026-10-13
```

### 11. eRisk / CLEF depression corpora (DepreSym and the severity tasks)

```yaml
catalogue:
  name: eRisk / CLEF depression corpora (DepreSym)
  source: https://erisk.irlab.org/depresym_dataset.html
  licence: "research use under a signed user agreement (data transfer agreement), no open licence"
  licence_evidence: https://erisk.irlab.org/depresym_dataset.html
  allowed_use: none
  languages: [en]
  size: "3,807,115 sentences from 3,107 Reddit users, annotated against BDI-II symptoms"
  label_scheme: "21 BDI-II depression symptoms, sentence-level relevance"
  known_biases: >
    Self-selected Reddit users of depression subreddits who chose to answer a
    questionnaire: not representative of people with depression, let alone of
    Manovia's users. A single self-reported score is used as the label.
  personal_data: true
  personal_data_notes: >
    Real users' posts about depression and self-harm, shared under a research
    agreement. Using this would require an ethics framework, a data agreement and
    a level of care (storage, deletion, access control) far beyond a Day-31
    repository, and the risk of a leachable label is exactly the harm Manovia's
    design is built to avoid.
  status: rejected
  verdict: DO NOT USE
  verdict_reason: >
    Agreement-gated and personal. Nothing from this family is downloaded,
    trained on, or evaluated against. If depression-severity modelling is ever
    pursued, it belongs behind a clinical partnership with ethics approval, not
    behind a download command.
  verified_on: 2026-10-13
```

### 12. Dreaddit and SMHD

```yaml
catalogue:
  name: Dreaddit / SMHD (self-reported mental-health diagnoses)
  source: https://aclanthology.org/2024.lrec-main.977.pdf (survey listing the corpora and their Reddit sources)
  licence: "none published that we could find; distributed by the authors on request"
  licence_evidence: https://aclanthology.org/2024.lrec-main.977.pdf
  allowed_use: none
  languages: [en]
  size: "Dreaddit: 3,553 Reddit posts (stress/no-stress); SMHD: user-level diagnosis labels"
  label_scheme: "binary stress; user-level mental-health diagnosis"
  known_biases: >
    Subreddit-based sampling: posts are written *about* stress to a support
    community, which is the opposite of the register Manovia sees. User-level
    diagnosis labels are self-reported and binary.
  personal_data: true
  personal_data_notes: >
    Reddit posts and inferred diagnoses: health data about individuals. Access is
    by author request, which makes the access trail itself a privacy obligation.
  status: rejected
  verdict: DO NOT USE
  verdict_reason: >
    No published licence found on 2026-10-13, and the content is health-related
    personal data. Either reason alone would stop it; both together mean it is
    not something a Manovia training run should touch.
  verified_on: 2026-10-13
```

### 13. Manovia's own fixtures (in-repo, synthetic)

```yaml
catalogue:
  name: Manovia fixtures (evals/datasets/*, ml/data/toy/*)
  source: https://github.com/BuildbyArindam/Manovia (evals/datasets/, ml/data/toy/)
  licence: "MIT (repository licence, LICENSE); authored in this repository"
  licence_evidence: ./LICENSE
  allowed_use: commercial
  languages: [en, hi, hi-Latn, bn]
  size: "evals: 572 crisis cases + 162 emotion cases + 60 response scenarios; ml toy fixture: 84 hand-written sentences"
  label_scheme: "repo-defined: risk levels, Manovia's nine emotions, rubric axes"
  known_biases: >
    Synthetic and hand-written by the project author plus generated review: no
    natural distribution, no real users, English/Devanagari/Bengali written by
    one or two people rather than crowds. It cannot represent either the
    distribution or the messiness of real user text.
  personal_data: false
  personal_data_notes: >
    No real people's words. The ml toy fixture is hand-written for tests; the
    eval sets are synthetic by construction (enforced for the crisis set by
    evals/build_crisis_dataset.py and its tests).
  status: used
  verdict: USE
  verdict_reason: >
    Owned by the project, MIT-licensed, no personal data: this is the only data
    that may be committed to Git and the only data CI can train on.
  verified_on: 2026-10-13
```

### 14. Manovia's hand-written Hindi/Bengali emotion set (planned)

```yaml
catalogue:
  name: Manovia hi/bn emotion set (planned, not yet written)
  source: "to be written in-repo under ml/data/planned/hi_bn_emotion/"
  licence: "MIT (repository licence, LICENSE); authored for Manovia"
  licence_evidence: ./LICENSE
  allowed_use: commercial
  languages: [hi, hi-Latn, bn]
  size: "target ≥ 9 emotions × 3 registers × 3 language variants × 25 rows ≈ 675 rows, plus a reviewed dev split"
  label_scheme: "Manovia's nine emotions (joy, sadness, anger, fear, anxiety, shame, loneliness, calm, neutral)"
  known_biases: >
    Authored, not observed: it will reflect the writers' registers and the
    situations they chose to write, which will be narrower and calmer than real
    use. It is a *review* corpus for the label scheme and the hi/bn plumbing, not
    a training corpus that could stand alone.
  personal_data: false
  personal_data_notes: >
    Written from invented everyday situations; the plan forbids transcribing
    anyone's real messages, and the dataset card requires a statement to that
    effect from every writer.
  status: planned
  verdict: USE WITH CONDITIONS
  verdict_reason: >
    Conditions of use are about process, not licence: two native speakers per
    language review every row independently, rows enter the set only when both
    agree on the label, disagreements go to a third reviewer, and the dataset
    card records reviewer initials and dates before any model is trained on it.
    Until then, status stays `planned` and no config may pin its hashes.
  verified_on: 2026-10-13
```

## Hindi and Bengali emotion data

**Finding (2026-10-13): there is no openly licensed, emotion-labelled,
Hindi-and-Bengali corpus that Manovia may use in a product.** What exists:

| candidate | language | licence | why it cannot go in the product |
| --- | --- | --- | --- |
| EmoNoBa | bn | CC BY 4.0 | *can* be used, with conditions — but comment-section register, and no anxiety/shame/loneliness |
| IndicSentiment / IndicGLUE | hi, bn | CC BY-NC 4.0 | non-commercial |
| EmoInHindi | hi | CC BY-NC-ND 4.0 | non-commercial, no derivatives |
| Bengali & Banglish | bn, bn-Latn | CC BY-NC 4.0 | non-commercial |
| SentiMix Hinglish | hi-Latn | paper CC BY 4.0, corpus by platform terms | polarity, not emotion; platform terms unread |
| emoHind-58K | hi | claims CC BY 4.0 via a third-party upload | provenance untraceable → DO NOT USE |

Two more candidates were searched and rejected without reading a licence,
because a corpus without a traceable original release is not a candidate: they
are listed in § Verification log.

### The plan: write and review a small set ourselves

Not a translation of an English set (translating emotional language produces
sentences no one writes) and not a scrape (personal data, platform terms). A
small, purpose-built set:

- **labels:** the nine emotions the product already speaks
  (`joy, sadness, anger, fear, anxiety, shame, loneliness, calm, neutral`).
- **variants:** `hi` (Devanagari), `hi-Latn` (romanised Hinglish), `bn`
  (Bengali script) — the three ways Manovia's users actually type.
- **size:** ≥ 25 rows per (label × variant) ≈ 675 rows, plus a dev split of ≥ 8
  per cell. Small on purpose: this is a review corpus for the label scheme, not a
  training set pretending to be one.
- **registers:** everyday first person; quoted speech from someone else; and
  indirect/somatic phrasing ("my chest feels tight and I cannot sit still") —
  the last is where the English analyser already fails on domain shift (Day-24
  finding), so the hi/bn set must expose it rather than avoid it.
- **authorship:** written by the project author and volunteer native speakers
  from invented situations. **No real messages, no transcripts, no personal
  data** — stated in writing on the dataset card by every contributor.
- **review:** two native speakers per language, independently, then compare.
  A row is accepted only when both reviewers pick the same label; disagreements
  go to a third reviewer and are recorded either way in the card. Reviewer
  initials and dates are recorded (never full names).
- **gates before use:** dataset card committed (from
  `ml/templates/DATASET_CARD.md`), `dvc add` for the bytes, native-review record
  complete, and only then may `ml/configs/hi_bn_handwritten_logreg.yaml` stop
  being a placeholder.

The draft card lives at
[`planned/hi_bn_emotion/DATASET_CARD.md`](planned/hi_bn_emotion/DATASET_CARD.md).

## Mental-health text corpora: the general answer

Every public mental-health text corpus looked at today is either
agreement-gated, non-commercial, or built from real people's disclosed distress
(eRisk/DepreSym, Dreaddit, SMHD, MentalChat16K's trial transcripts). Manovia's
answer is **none of them**, for three reasons that are about the product, not
about being timid:

1. **Consent scope.** A person who posted to a support forum under a platform's
   terms did not consent to their words training a companion app.
2. **Label harm.** A corpus labelled with inferred diagnoses teaches a model to
    *infer diagnoses*, which is precisely what Manovia has committed never to do
    (AGENTS.md non-negotiable rule 2).
3. **Leakage.** Distress text is highly identifying; a model trained on it can
    reproduce a person's situation in a way that is worse than a database leak
    because it is unauditable.

What Manovia uses instead: its own synthetic crisis/emotion eval sets, its own
hand-written sets, and expert-reviewed content — see the repository's
`evals/datasets/README.md` for the crisis and rubric sets.

## Instruments (not datasets)

`PHQ-9` and `GAD-7` are used by the product as instruments (Day 20), under their
publishers' terms, and are not part of any ML training set. They are listed here
only so that nobody later mistakes "we have PHQ-9 scores" for "we have a
labelled depression dataset".

## Verification log

What was actually read on 2026-10-13 (all licences above were verified from
these pages, not from memory):

| # | source read | what it established |
| --- | --- | --- |
| 1 | HF dataset card `google-research-datasets/go_emotions` | Apache-2.0, 58,009 rows, 27+neutral |
| 2 | Kaggle `saifsust/emonoba` + ACL Anthology 2022.aacl-short.17 | EmoNoBa exists, CC BY 4.0, test split 2,272 |
| 3 | Kaggle `debadityashome/emohi58k-…` | licence claim exists, original release absent |
| 4 | indicnlp.ai4bharat.org/indic-glue | IndicNLP Suite is CC BY-NC 4.0 |
| 5 | ResearchGate record for EmoInHindi (LREC 2022) | CC BY-NC-ND 4.0 |
| 6 | SemEval-2020 Task 9 overview (ACL Anthology) + task site | paper CC BY 4.0; corpus via CodaLab task terms |
| 7 | Data in Brief 2024 article record | CC BY-NC 4.0 |
| 8 | parl.ai/docs/tasks.html + HF card `facebook/empathetic_dialogues` | CC BY-NC 4.0 |
| 9 | GitHub `thu-coai/Emotional-Support-Conversation` | "academic research use only" |
| 10 | HF card + GitHub `PennShenLab/MentalChat16K` | MIT label on Hub; PISCES trial transcripts inside |
| 11 | erisk.irlab.org/depresym_dataset.html | research use under user agreement |
| 12 | LREC 2024 survey listing Reddit mental-health corpora | Dreaddit/SMHD exist; no licence published |

### 15. Manovia synthetic safety corpus (expanded from the Day-9 seeds)

```yaml
catalogue:
  name: Manovia synthetic safety corpus (expanded from the Day-9 seeds)
  source: "generated in-repo by ml/src/manovia_ml/training/safety/build_data.py from evals/datasets/crisis_cases_{train,dev}.jsonl"
  licence: "MIT (repository licence, LICENSE); authored in this repository"
  licence_evidence: ./LICENSE
  allowed_use: commercial
  languages: [en, hi, hi-Latn, bn]
  size: "456 seeds -> 3,445 kept rows (127 dropped as rules_inconsistent); splits train 2,756 / dev 365 / calibration 358"
  label_scheme: "five risk levels (none < low < medium < high < imminent) plus seven categories"
  known_biases: >-
    Synthetic by construction, and the expansion is templated: the same seed
    produces several rows through a fixed set of paraphrase and adversarial
    transforms, so the corpus is far less lexically diverse than its row count
    suggests. Max Jaccard similarity against the frozen test split is 0.72
    (a paraphrase of a train seed against a near-identical test row) with zero
    verbatim overlap; that similarity is inherited from the hand-written Day-9
    corpus, not introduced here. It cannot represent real crisis language, and a
    number measured on it is a number about this corpus.
  personal_data: false
  personal_data_notes: >-
    No real people's messages, by rule. The seeds are the hand-written synthetic
    Day-9 cases; the paraphraser is a Fake in CI and the LLM provider in a real
    run, and neither is ever given a real user message. Provenance travels on
    every row (seed_id, source, transforms, rules_level, rules_agrees), so any
    row can be traced back to the seed it came from and dropped.
  status: used
  verdict: USE
  verdict_reason: >-
    Owned by the project, MIT-licensed, no personal data, and generated
    reproducibly from a committed seed file with a fixed seed — which is what
    makes a 3,445-row corpus checkable without committing a single byte of it.
    The frozen test split is never read by the builder: it is an input to the
    evaluator only, its hash lives in evals/datasets/manifest.json, and the
    trainer refuses a corpus that leaks a training row into it.
  verified_on: 2026-10-13
```

Candidates rejected before a licence read (no traceable original release found,
or nothing but a vendor/blog repost): "Hindi Emotion Dataset" reposts on Kaggle
without an author, and several "Bengali emotion" GitHub gists. They are recorded
here so the same search is not repeated from scratch.

**Next verification due:** 2027-04-11 (180 days), or sooner if any entry's
status changes from `candidate` to `used`.

### 16. Manovia retrieval eval set (Day 35)

```yaml
catalogue:
  name: Manovia retrieval eval set (hand-written + template-paraphrased)
  source: "hand-written rows committed at ml/data/retrieval/retrieval_queries.jsonl; synthetic rows generated in-repo by ml/src/manovia_ml/retrieval/augment.py (DVC stage retrieval_expand)"
  licence: "MIT (repository licence, LICENSE); authored in this repository"
  licence_evidence: ./LICENSE
  allowed_use: commercial
  languages: [en, hi, hi-Latn, bn, bn-Latn]
  size: "154 hand-written rows -> 254 expanded (100 synthetic, 12 candidates screened out); splits train 97 / dev 85 / calibration 20 / test 52"
  label_scheme: "1-3 relevant knowledge-document ids per answerable row, or has_answer=false with a no_answer_reason (out-of-scope 42, corpus-gap 17); relevance is topical, not language-matched"
  known_biases: >-
    One author, so one view of how distress is phrased; the topic mix follows the
    40-document corpus rather than real traffic (exams, sleep and breathing
    over-represented, relationships and money represented only as corpus-gap
    no-answer rows). The hi/bn/bn-Latn rows have no native-speaker review yet —
    the limitation translations/STATUS.md already records for product copy. The
    synthetic rows are template paraphrases of hand-written seeds: they inherit
    their labels (which is why they are usable at all) and are lexically closer
    to the corpus than a real first draft, which is why they are kept out of the
    test split entirely.
  personal_data: false
  personal_data_notes: >-
    No real people's messages. Crisis and self-harm content is deliberately
    absent: the deterministic crisis gate runs before retrieval (AGENTS.md rule
    1), so those turns never reach a retriever, and including them would make
    the abstain metric look like a safety mechanism it is not. Provenance travels
    on every synthetic row (source, generator, seed_id), so any of them can be
    traced to the hand-written row it came from and dropped.
  status: used
  verdict: USE
  verdict_reason: >-
    Owned by the project, MIT-licensed, no personal data, and the derived half is
    reproducible from a committed seed file with a fixed seed — so the 254-row
    eval set is checkable without committing its bytes. The test split is
    hand-written only, is read by the evaluator and nothing else, and its content
    fingerprint is recorded in ml/data/retrieval_splits/manifest.json so any
    tuning against it changes a hash someone is watching.
  verified_on: 2026-10-14
```
