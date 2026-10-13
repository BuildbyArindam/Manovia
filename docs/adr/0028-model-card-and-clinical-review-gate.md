# ADR 0028: Model card and clinical-review checklist as documentation gates

- Status: accepted (Day 28)
- Date: 2026-10-13
- Deciders: Manovia maintainers
- Supersedes: none
- Related: [0009 safety ML ensemble](0009-safety-ml-ensemble.md), [0024 evaluation harness](0024-evaluation-harness.md), [docs/model-card.md](../model-card.md), [docs/clinical-review.md](../clinical-review.md)

## Context

The product uses several models: a safety classifier, an emotion analyser with a
lexicon fallback, and language-model providers, plus screening instruments and
retrieval. Before Day 28 the repository described these in several places, and some
of the descriptions were wrong. The old model card named OpenAI as a provider, but
the configuration accepts only `anthropic`, `ollama` and `fake`. The evaluation
numbers were spread across reports. Nothing said who had reviewed the clinical
content, or that nobody had.

## Decision

- `docs/model-card.md` is the single place that describes each model: its intended
  and out-of-scope use, training and evaluation data, the metrics with their
  intervals, the known failure modes, the ethical limits and an update policy.
  Numbers are taken from the evaluation reports, and each one should be traceable
  to a file.
- A figure that has not been checked against a source file is not published. The
  Day 28 verification note lists every such figure in the card so that a reader
  can see what is still unchecked.
- The card does not claim clinical validity. The frozen test split had been scored
  before the card was written, so its figures are reported as non-clean.
- `docs/clinical-review.md` is a checklist, not a sign-off sheet. Every item is
  open. Each item names the file and key it concerns. Nothing is marked reviewed
  until a named licensed professional or native speaker signs it.
- The model card does not hard-code a model identifier. `backend/tests/llm/test_no_hardcoded_model.py`
  enforces that, so the production Claude version is not recorded in the
  repository. The deployment configuration is the place to record it.

## Consequences

- Launch cannot be justified from the repository alone. The checklist and the
  card say which items a clinician or native speaker still has to complete.
- When a model, threshold or dataset changes, the card and checklist need the same
  change in the same commit. The update policy in the card sets that rule.
- Some figures are reported with their limits rather than as headline claims: the
  emotion analyser's sentiment fallback, the unmeasured RoBERTa checkpoint, and the
  unreviewed Hindi and Bengali translations.

## Alternatives considered

- **Keep the facts in each feature ADR.** Rejected: nobody reads a dozen records to
  learn what the product's models can do.
- **Mark the checklist as reviewed for the portfolio.** Rejected: nobody has
  reviewed it.

## Verification

- Table checks on both documents report zero problems.
- The card's provider list was checked against the configuration choices in
  `backend/app/core/config.py`. The no-hard-coded-model claim was checked against
  `backend/tests/llm/test_no_hardcoded_model.py`.
- Figures that could not be checked are listed in the Day 28 verification note.
