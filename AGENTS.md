# Manovia — Agent Operating Rules

## Mission
Build a privacy-first, safety-first mental wellbeing SELF-HELP companion. It is NOT therapy,
NOT a diagnostic tool, and NOT a crisis service.

## Non-negotiable safety rules
1. Crisis/self-harm detection runs BEFORE any LLM call. High-risk messages get a deterministic,
   pre-written response with helplines. The LLM never improvises at high risk.
2. Never diagnose, never recommend or discuss medication doses, never promise outcomes.
3. Never claim to be a therapist or a human. Disclose AI nature in onboarding and settings.
4. Every LLM output passes an output-safety check before reaching the user.
5. Never log raw message text at INFO level or above. Never send identifiers to the LLM.
6. Helpline data lives only in backend/app/content/helplines.json with a last_verified date.

## Engineering rules
- Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic. React + Vite + TypeScript + Tailwind.
- Type hints everywhere; ruff + mypy clean; no TODOs left without an issue note in PROGRESS.md.
- Every feature ships with tests (unit + at least one integration test). No feature without tests.
- Small commits, conventional commit messages (feat:, fix:, test:, docs:, chore:).
- Config only via environment variables (see .env.example). Never hard-code secrets or model names.
- Every external dependency (LLM, HF model) sits behind an interface with a Fake for tests;
  the test suite must pass fully offline.
- Accessibility: keyboard navigable, ARIA labels, contrast >= 4.5:1, respects prefers-reduced-motion.
- Tone of user-facing copy: warm, plain, short, non-judgemental. No toxic positivity.

## Working agreement for each session
1. Read AGENTS.md and PROGRESS.md first.
2. Do only the day's scope; list anything out of scope in PROGRESS.md under "Parking lot".
3. Run lint + tests before declaring done. Paste actual command output summaries, never guess.
4. Update PROGRESS.md (what was done, decisions, known issues, next steps).

NOTES FOR THIS SANDBOX:
- The repository may still be called MentalHealthSupport. Do NOT rename it; I will rename it on GitHub myself.
- If I attached Manovia_49_Day_Plan.md to this message, commit it unchanged to docs/plan/Manovia_49_Day_Plan.md.

## ML ground rules (Day 31 — apply to everything under `ml/`)

These extend the rules above; where they overlap, the stricter rule wins.

1. **Safety ordering is untouched by ML.** Crisis detection stays deterministic
   and runs before any model call (rule 1). A model may only ever *raise*
   caution through the reviewed interfaces (`docs/adr/0009-safety-ml-ensemble.md`);
   it never lowers a level, never writes the crisis card, and never sees raw
   user identifiers. `ml/` does not deploy anything to users.
2. **No GPU in this sandbox.** Training code, configs, tests and a *tiny* smoke
   run on hand-written toy data happen here. Real training runs happen later on
   free Colab/Kaggle from the ready-to-run notebook/script in `ml/notebooks/`
   plus the exact instructions in `ml/README.md`.
3. **Never commit model weights or datasets.** No `.bin`, `.pt`, `.safetensors`,
   `.csv`, `.parquet`, `.joblib`, `.pkl`, `.onnx`, archive files or downloaded
   corpora in Git, ever — under any directory, including `ml/`. Committed:
   configs, code, tests, report templates, dataset/model *cards*, and the
   catalogue. Dataset bytes are versioned with DVC (`ml/dvc.yaml`) and live in a
   cache outside the repository's tracked files; reports quote hashes, not data.
   The only JSONL under `ml/data/` that is committed is the hand-written toy
   fixture used by the tests and the smoke run.
4. **Every dataset is catalogued before it is used.** `ml/data/CATALOGUE.md`
   records, per dataset: name, source URL, licence, allowed use
   (research/commercial), languages, size, label scheme, known biases, and
   whether it contains personal data. Unverified or restrictive licences are
   marked **DO NOT USE** and are not downloaded, trained on, or evaluated
   against. A licence claim in a mirror (Kaggle/HF re-upload) is not evidence
   about the original release.
5. **Every run is reproducible and logged.** Each run logs the git commit (and
   dirty flag), the dataset hash, the resolved config, the seed, library
   versions, metrics and artefacts to MLflow (local file store by default).
   The same config + seed must reproduce metrics within the config's tolerance;
   a run that cannot be reproduced is a bug, not a nuisance.
6. **Config is data, not code.** Hyperparameters, paths, seeds and tolerances
   live in `ml/configs/*.yaml`, validated by pydantic (unknown keys are
   errors). Code must not hard-code a hyperparameter that a config can express.
7. **Notebooks are exploration only.** Anything reusable moves into
   `ml/src/manovia_ml/` with a test. A notebook may not be the only home of a
   decision.
8. **No personal data, ever.** No real user text, no scrape of Manovia data, no
   re-identification attempts, no downloading corpora that contain personal data
   (see the catalogue's personal-data column) into this repository.
9. **Cards before claims.** A model that is going to be described anywhere
   public needs a model card (`ml/templates/MODEL_CARD.md`) and its data needs a
   dataset card (`ml/templates/DATASET_CARD.md`). Numbers without a card and a
   committed run id are anecdotes.
10. **`ml/` stays offline.** Tests, the smoke run and CI never call the network:
    toy fixtures and local files only. Network access belongs in explicit,
    documented download/Colab steps run by a human on their own machine.
