# ADR 0048 — Release verification, reproducible reports and what a v2.0 claim may rest on

Date: 2026-10-20. Status: accepted.

## Context

Day 49 is the v2.0 release: run everything, fix regressions, write the technical
report and the README's AI/ML section, tag the release, and refresh the career
material. The verification environment is not the target environment — it has no
Docker, no browser, no GPU, no microphone, no headset and no Hugging Face egress —
so the release had to decide three things before it could make any claim:

1. **What "verified" means** when a check cannot run in the build environment.
2. **Where a number lives**, given that the MLflow store is local and not
   published.
3. **Which release artefacts are allowed to be produced by an agent** and which
   must be produced by the repository owner.

Running the verification also turned up defects that had been invisible to CI:
two tests that read a gitignored DVC output, a fairness gate that silently used
the wrong ceiling, a fairness report seeded from a salted `hash()`, DVC manifests
carrying a wall-clock date and absolute paths, and an environment-dependent mypy
failure. Each is a small decision in itself, and each belongs on the record.

## Decisions

### 1. A check that cannot run is reported as "not run", never as "passed"

The verification note ([docs/verification/day49-verification.md](../verification/day49-verification.md))
has a three-state result — PASS / FAIL / **NOT RUN** — and the release notes repeat
the "NOT RUN" list. Concretely, for v2.0: Playwright journeys, Lighthouse/Core Web
Vitals and real-device/voice/XR/WebGPU paths are **not run** here, with the reason
(their CDNs and hardware are unavailable to the build sandbox) and the command a
human should run. Those checks are configured in `.github/workflows/`; nothing in
the release claims they were executed on this commit.

To keep the browser-dependent gap as small as possible, the parts of the journey
that *are* an HTTP contract are driven by
[`scripts/verify_day49_journey.py`](../../scripts/verify_day49_journey.py): one
real guest account, one real database, thirteen steps from onboarding to account
deletion. It is deliberately not a substitute for the Playwright suite and says
so in its own docstring.

### 2. A number is published only with a committed report and a run id

Every row of the README's AI/ML table points at an artefact the repository owns: a
report under `evals/reports/<date>/` or `ml/reports/`, a verification note, or a
config. MLflow run ids are quoted in the report headers, because the MLflow store
is a local file store and publishing it is a deployment decision, not a release
step. Rows that have no artefact were removed rather than softened (see the
mapping exercise in the verification note).

### 3. Reproducibility is a property of the artefact, not of the author's machine

Three fixes, each with a regression test that fails if it comes back:

- a wall-clock date and absolute seed paths were removed from the safety
  manifests, and a test asserts byte-stability of two builds;
- the fairness stand-in predictors seed from SHA-256 instead of the per-process
  salted `hash()`, and a test runs the gate script in two processes with different
  `PYTHONHASHSEED` values and compares the report bytes;
- category names are normalised between `release_gates.yaml` and the template
  categories, and a test asserts that **every** template category has a configured
  ceiling (the failure mode of a mismatch is always the permissive direction).

### 4. A test may not depend on an artefact the pipeline has not produced

Two splitter tests read `ml/data/safety_toy/train.jsonl`, a DVC output that does
not exist in a fresh clone, so `make ml-test` was red out of the box. They now
read the committed fixture, which is also the stronger test (the generated file
had already been split, so re-splitting it proved less).

### 5. The release tag is created by the owner, not by the agent

An agent may prepare `CHANGELOG.md`, `docs/releases/v2.0.0.md` and a local
annotated tag, but publishing a release is an owner action: it triggers
`.github/workflows/release.yml`, which runs the migration round-trip against
Postgres, builds and pushes images to GHCR and creates the GitHub release. The
exact commands to publish are in the verification note.

### 6. Research that loses is reported as a result

The neural mood models were trained and measured for this release; the best one
lost to a seven-day moving average (MAE 0.561 vs 0.554). The README, the
changelog, the technical report and the career material all say so, in the same
table as the wins. A release notes document that only lists successes is a
marketing artefact, and this project cannot afford one.

## Consequences

- The verification note is longer than a checklist, because "not run" needs a
  reason and a command.
- CI gains nothing automatically from the fixes above except honesty: the
  reproducibility tests live in `ml/tests/`, the manifest test in
  `backend`/`ml` suites, and the browser checks stay in the existing GitHub
  Actions jobs.
- A future release must repeat the three-state table, or the claim "verified"
  becomes unfalsifiable.
- `PROGRESS.md` still carries historical duplication from earlier days; this
  release appends rather than rewriting history.

## Verification

- [docs/verification/day49-verification.md](../verification/day49-verification.md) —
  the PASS/FAIL/NOT RUN table, the removed README numbers, the kill-switch and
  transparency checks, and the human hand-off list.
- `ml/tests/test_fairness_audit.py` — cross-process byte stability and threshold
  coverage.
- `ml/tests/test_safety_data.py::test_the_built_manifest_is_byte_stable`.
- `python3 scripts/verify_day49_journey.py` — 13/13 steps against a running stack.
- `make lint`, `make test`, `make eval`, `make eval-agent`, `make ml-test`.
