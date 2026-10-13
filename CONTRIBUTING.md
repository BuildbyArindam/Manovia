# Contributing to Manovia

Thank you for wanting to help. Manovia is a **privacy-first, safety-first
self-help companion**. It is not therapy, not a diagnostic tool and not a crisis
service. Read [AGENTS.md](AGENTS.md) first: its non-negotiable safety rules
decide what a change may and may not do, and they outrank everything below.

## Before you start

1. Read [AGENTS.md](AGENTS.md) (safety and engineering rules) and the latest
   entry in [PROGRESS.md](PROGRESS.md) (what is built, what is known to be wrong).
2. For anything that touches crisis detection, the helpline data, screening
   instruments, or model thresholds, **open an issue first**. These changes need
   clinical review before they merge (see [docs/clinical-review.md](docs/clinical-review.md)).
3. Use the issue templates in `.github/ISSUE_TEMPLATE/`.

## Ground rules (from AGENTS.md)

- **Never weaken a safety gate to make a test pass.** A failing crisis test means
  the code is wrong or the test found a real gap. Fix the root cause, add a
  regression test, and say so in the pull request.
- **Crisis detection runs before any model call.** A change that lets a model
  answer a HIGH or IMMINENT message is rejected.
- **No raw message text in logs** at INFO or above, and **no identifiers sent to
  an LLM provider**. The test suite has sentinel tests for both; do not remove them.
- **Helpline data lives only in `backend/app/content/helplines.json`**, with a
  `source_url` and `last_verified` date for every entry. Do not add numbers
  anywhere else.
- **Config comes only from environment variables** (see `.env.example`). Never
  commit a secret, a real key, a real person's words or a real phone number in a
  test fixture. Use synthetic data.
- **Every external dependency sits behind an interface with a fake.** The test
  suite must pass fully offline.

## Development setup

Backend (Python 3.11+):

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Frontend (Node 20.19+):

```bash
cd frontend
npm ci
```

The full stack runs with Docker; see the quick start in [README.md](README.md).

## Before you open a pull request

Run the same checks CI runs. From the repository root:

```bash
make lint      # ruff check + ruff format --check + mypy (backend), ESLint (frontend)
make test      # pytest with the coverage gates, then Vitest
```

Also, where relevant:

- `make eval` — the safety, emotion and response evaluation gates
  (`evals/thresholds.yaml`). A drop in a gated number fails the build.
- `make db-check` — `alembic check`, so model changes ship with a migration.
- `npm run format:check` in `frontend/` — Prettier.

If you change a documented number, a claim in the README, or a file under
`docs/`, check that the claim still holds and that the link still resolves.

## Commits and branches

- Small commits, [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`.
- Branch from `main`. Keep one topic per pull request.
- Do not commit generated artifacts (coverage files, local SQLite databases,
  `.env` files, screenshots of real people). Keep large datasets out of Git.

## Architecture decisions

Any decision that changes how the system behaves, stores data, or makes a
safety trade-off needs an ADR in [docs/adr/](docs/adr/). Copy the most recent
ADR as a template, give it the next free number, and link it from the pull
request. [docs/adr/README.md](docs/adr/README.md) is the index.

## Translations

Hindi and Bengali copy is not yet reviewed by native-speaker reviewers. See
[translations/STATUS.md](translations/STATUS.md) for the review state. Do not
mark a translation as reviewed without a named reviewer and a date.

## Reporting security problems

Do **not** open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Licence

By contributing you agree that your contribution is licensed under the
repository's [MIT licence](LICENSE).
