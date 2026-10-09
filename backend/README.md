# Backend

FastAPI backend for Manovia: environment-based configuration, structured JSON
logging with request IDs and log redaction, a consistent error envelope, security
headers, CORS, liveness/readiness endpoints, the Day 3 data layer (SQLAlchemy 2
models, Alembic migrations, async sessions, thin repositories, and field
encryption for personal text), and the Day 6 NLP service (emotion and
sentiment analysis behind one interface, with lexicon fallbacks).

## Setup

```bash
cd backend
pip install -e ".[dev]"   # or: uv sync
cp ../.env.example .env   # then fill in DATABASE_URL and FIELD_ENCRYPTION_KEY
alembic upgrade head      # create the schema (or: make -C .. db-upgrade)
```

Local development works with no configuration at all: `DATABASE_URL` defaults to
`sqlite:///./manovia.db`. Writing anything personal needs `FIELD_ENCRYPTION_KEY`
(generate a throwaway one with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).

## Commands

| Command                 | What it does                                              |
| ----------------------- | --------------------------------------------------------- |
| `make dev`              | Run the API on http://localhost:8000 (uvicorn, reload)     |
| `make test`             | Run pytest with a coverage summary                         |
| `make lint`             | ruff (check + format check) and mypy                       |
| `make format`           | Apply ruff formatting and safe lint fixes                  |
| `make migrate`          | `alembic upgrade head`                                     |
| `make migrate-down`     | `alembic downgrade base`                                   |
| `make migrate-check`    | Fail if the models and the migrations have drifted         |
| `make revision m="..."` | Autogenerate the next migration from the models             |

## Layout

- `app/main.py` — application factory (`create_app()`); builds the `Database`,
  installs the field cipher, disposes the engine on shutdown
- `app/core/config.py` — pydantic-settings configuration (reads `.env`); validates
  `DATABASE_URL` and requires `SECRET_KEY` + `FIELD_ENCRYPTION_KEY` in production
- `app/core/crypto.py` — `FieldCipher` protocol + `FernetCipher`; the only code that
  turns personal text into ciphertext and back
- `app/core/logging.py` — structlog JSON logging, log redaction, request-ID middleware
- `app/core/errors.py` — error envelope `{"error": {"code", "message", "request_id"}}`
- `app/core/middleware.py` — security headers middleware
- `app/models/` — one typed SQLAlchemy 2 model per table, plus `base.py` (naming
  convention, portable column helpers) and `enums.py`
- `app/db/session.py` — async engine + session factory, SQLite/PostgreSQL URL
  handling, `PRAGMA foreign_keys`, readiness probe helper
- `app/db/repos/` — the only place statements are built; they never commit
- `app/api/deps.py` — `Depends` helpers (`get_session`, `get_database`)
- `app/api/v1/health.py` — `GET /api/v1/health` (liveness), `GET /api/v1/ready`
  (config + database)
- `app/api/v1/crisis.py` — `GET /api/v1/crisis/resources`: the helpline list from
  `app/content/helplines.json`, **public** (no auth, no consent gate — someone in
  trouble has not signed in), validated on load with a `last_verified` date
  (AGENTS.md safety rule 6)
- `app/services/nlp/` — the emotion and sentiment service:
  `base.py` (the `EmotionAnalyzer` contract, the nine-label taxonomy and the
  valence/arousal anchors), `hf.py` (the Hugging Face model: lazy thread-safe
  load, batching, truncation, and `LABEL_MAP`), `keyword.py` and `sentiment.py`
  (the lexicon fallbacks), `lexicon.py` (shared negation/intensifier rules),
  `language.py` (`en`/`hi`/`bn`/`other` plus Hinglish heuristics), `cache.py`
  (an LRU keyed by a hash of the text, never the text), `chain.py` (the
  fallback chain, circuit breaker and latency governor), `fake.py` (the
  deterministic test double) and `__init__.py` (`build_analyzer()`)
- `app/api/v1/dev.py` — `POST /api/v1/dev/analyze` and
  `GET /api/v1/dev/analyze/state`. **Dev-only**: mounted only when
  `APP_ENV != production`, so in production the route does not exist (404) and is
  absent from the OpenAPI schema. Logs a SHA-256 fingerprint and a length, never
  the text (AGENTS.md safety rule 5)
- `app/content/` — the two shipped content files and their loaders:
  `consent_documents.json` (+ `documents.py`) and `helplines.json` (+ `crisis.py`)
- `alembic/` — migration environment (`env.py` reads `DATABASE_URL` through
  `Settings`) and `versions/0001_initial_schema.py`
- `tests/unit`, `tests/integration` — pytest suites; integration tests run against a
  real SQLite file, fully offline

## NLP service (Day 6)

```bash
pip install -e ".[dev,nlp]"   # the nlp extra adds transformers + torch
```

The extra is **optional**: without it the module still imports and the lexicon
analyzers answer. `EMOTION_ANALYZER` picks what leads the chain (`auto` / `hf` /
`keyword` / `sentiment` / `fake`); the lexicons are always attached as fallbacks,
and `EMOTION_MODEL_ID` names the checkpoint. See
[`docs/adr/0006-emotion-model.md`](../docs/adr/0006-emotion-model.md) for the
model choice, the label mapping and the licence.

```bash
curl -s -X POST localhost:8000/api/v1/dev/analyze \
  -H 'content-type: application/json' \
  -d '{"text":"exam kal hai, bahut dar lag raha hai"}' | python3 -m json.tool
```

```json
{
  "primary": "fear",
  "valence": -0.6,
  "arousal": 0.7,
  "analyzer": "keyword",
  "language": {"lang": "hi", "confidence": 1.0, "hinglish": true, "script": "latin"},
  "cached": false,
  "duration_ms": 0.3
}
```

`analyzer` says which of the chain answered - `hf`, `keyword`, `sentiment`,
`fake`, or `unavailable` if everything failed - so a silent degradation is
visible in the response rather than in a guess.

The real model needs the hub; the offline suite covers the pipeline anyway by
building a tiny local checkpoint (`tests/integration/test_nlp_hf_pipeline.py`).
Run the real one with `pytest -m model -q`.

## Schema notes

- Erasing a person is `DELETE FROM users WHERE id = ?`: every child table has
  `ON DELETE CASCADE`, and `messages` cascades from `chat_sessions`.
- Free text is never stored in the clear: `messages.content_encrypted`,
  `journal_entries.title_encrypted` / `body_encrypted`, and
  `mood_entries.note_encrypted` are ciphertext blobs with no plaintext sibling.
- `safety_events` has no textual column: ids, a risk tier, a source, a timestamp.
- `tests/unit/test_models.py` pins those invariants, and
  `tests/integration/test_migrations.py` fails if the models and the migration drift.

Configuration comes from environment variables only (see `../.env.example`).
