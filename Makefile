.PHONY: dev test lint format db-upgrade db-downgrade db-check seed ingest retention frontend-dev frontend-test frontend-lint frontend-format eval up down smoke prod-up prod-down prod-migrate ml-install ml-data ml-status ml-lock ml-catalogue ml-train ml-record ml-eval ml-report ml-smoke ml-test ml-lint ml-fetch-goemotions ml-mlflow-up ml-mlflow-down ml-emotion-smoke ml-emotion-train ml-emotion-eval ml-emotion-tune ml-emotion-data ml-retrieval-finetune ml-retrieval-tune ml-retrieval-rerank ml-retrieval-calibrate ml-retrieval-eval ml-retrieval-smoke ml-retrieval-pipeline ml-mood-forecast ml-jitai-sim ml-jitai-smoke

MOOD_FORECAST_CONFIG ?= configs/mood_forecast.yaml
MOOD_FORECAST_ARGS ?=

# Backend targets delegate to backend/; frontend targets to frontend/. Docker
# targets (up/down/smoke) drive the compose stack and were wired on Day 7;
# `eval` runs the Day 9 safety evaluation and `ingest` the Day 13 knowledge
# ingestion (backend/Makefile).

dev:
	$(MAKE) -C backend dev

test:
	$(MAKE) -C backend test
	npm --prefix frontend run test:run

lint:
	$(MAKE) -C backend lint
	npm --prefix frontend run lint

format:
	$(MAKE) -C backend format
	npm --prefix frontend run format

# Database. Migrations run from backend/ so alembic.ini resolves; the demo seed
# runs from the repository root and writes to DATABASE_URL (default
# sqlite:///./manovia.db, i.e. ./manovia.db here).
db-upgrade:
	$(MAKE) -C backend migrate

db-downgrade:
	$(MAKE) -C backend migrate-down

db-check:
	$(MAKE) -C backend migrate-check

seed:
	python3 scripts/seed_demo_data.py

# Day 22 retention job. Deletes personal data older than each person's own
# retention window (Settings → Privacy). Always dry-run first:
#   make retention DRY_RUN=1
# Add JSON=1 for a machine-readable report. Needs the same DATABASE_URL and
# FIELD_ENCRYPTION_KEY as the API. Point PYTHON at the interpreter that has the
# backend installed (the venv) when it is not the one on PATH:
#   make retention PYTHON=.venv/bin/python DRY_RUN=1
PYTHON ?= python3
DRY_RUN ?=
JSON ?=
retention:
	$(PYTHON) scripts/apply_retention.py $(if $(DRY_RUN),--dry-run,) $(if $(JSON),--json,)

# Frontend. `frontend-dev` proxies /api to the backend on port 8000, so run
# `make dev` in another terminal first.
frontend-dev:
	npm --prefix frontend run dev

frontend-test:
	npm --prefix frontend run test:run

frontend-lint:
	npm --prefix frontend run lint

frontend-format:
	npm --prefix frontend run format

# Docker dev stack (Day 7): api + web + postgres. `make up` builds and starts
# everything in the background (the api container auto-migrates in dev);
# `make smoke` runs the end-to-end smoke test against it; `make down` stops
# the stack and keeps the database volume (use `docker compose down -v` to
# wipe it).
up:
	docker compose up -d --build

down:
	docker compose down

smoke:
	./scripts/smoke.sh

# Production-shaped stack (Day 27): same images, APP_ENV=production, and the
# migration as a one-shot `migrate` service that the api container waits on.
# Needs SECRET_KEY and FIELD_ENCRYPTION_KEY in ./.env (compose refuses to
# start without them). Rehearses the Fly release sequence locally:
#   prod-up        build + migrate + start (api only, no dev web container)
#   prod-migrate   run the release migration again (idempotent, upgrade head)
#   prod-down      stop the stack (keeps the volume)
COMPOSE_PROD := docker compose -f docker-compose.yml -f docker-compose.prod.yml

prod-up:
	$(COMPOSE_PROD) up -d --build --exit-code-from migrate
	$(COMPOSE_PROD) logs -f api

prod-migrate:
	$(COMPOSE_PROD) run --rm migrate

prod-down:
	$(COMPOSE_PROD) down

eval:
	$(MAKE) -C backend eval

# Day 13 knowledge ingestion (see backend/Makefile for the notes).
ingest:
	$(MAKE) -C backend ingest

# Day 31 ML platform. ml/Makefile owns the details (including which interpreter
# to use); these are here so the repository has one entry point.
#   make ml-smoke                                  the full CI path, seconds, offline
#   make ml-train CONFIG=configs/toy_emotion_sgd.yaml
#   make ml-eval  RUN=<run id>       make ml-report RUN=<run id>
#   make ml-data  (= dvc repro)      make ml-status (= lock check + dvc status)
#   make ml-catalogue                                 licence + verdict check
#   make ml-install                                   pip install -e "./ml[ml,dev]"
#   make ml-test  /  make ml-lint                     pytest / ruff + mypy
#
# Day 32 (emotion LoRA). `ml-emotion-smoke` is the offline one: tiny random-init
# XLM-R, seconds, no network. Real runs need a GPU (ml/notebooks/README.md).
#   make ml-emotion-smoke
#   make ml-emotion-train CONFIG=configs/emotion_lora_xlmr.yaml
#   make ml-emotion-eval  ARTIFACT=artifacts/emotion/manovia-emotion-xlmr-v1
#   make ml-emotion-tune  CONFIG=... TRIALS=12
ml-install:
	$(MAKE) -C ml install

ml-data:
	$(MAKE) -C ml data

ml-status:
	$(MAKE) -C ml status

ml-lock:
	$(MAKE) -C ml lock

ml-catalogue:
	$(MAKE) -C ml catalogue

ml-catalogue-write:
	$(MAKE) -C ml catalogue-write

ml-train:
	$(MAKE) -C ml train CONFIG=$(CONFIG)

ml-record:
	$(MAKE) -C ml record CONFIG=$(CONFIG)

ml-eval:
	$(MAKE) -C ml eval RUN=$(RUN)

ml-report:
	$(MAKE) -C ml report RUN=$(RUN)

ml-smoke:
	$(MAKE) -C ml smoke

# Day 35: fine-tuned retrieval (ADR 0035). `ml-retrieval-pipeline` is the whole
# chain in order; the individual targets are what a re-fit after a corpus change
# runs. See ml/Makefile for which stages need the backend importable.
ml-retrieval-finetune:
	$(MAKE) -C ml retrieval-finetune

ml-retrieval-tune:
	$(MAKE) -C ml retrieval-tune

ml-retrieval-rerank:
	$(MAKE) -C ml retrieval-rerank

ml-retrieval-calibrate:
	$(MAKE) -C ml retrieval-calibrate

ml-retrieval-eval:
	$(MAKE) -C ml retrieval-eval

ml-retrieval-pipeline:
	$(MAKE) -C ml retrieval-pipeline

ml-retrieval-smoke:
	$(MAKE) -C ml retrieval-smoke

# Day 36: run the synthetic mood-forecast experiment. A baseline-only result
# requires the explicit `MOOD_FORECAST_ARGS=--allow-baselines-only` opt-in.
ml-mood-forecast:
	$(MAKE) -C ml mood-forecast MOOD_FORECAST_CONFIG=$(MOOD_FORECAST_CONFIG) MOOD_FORECAST_ARGS="$(MOOD_FORECAST_ARGS)"

# Day 37 synthetic contextual-bandit run and tiny smoke variant.
ml-jitai-sim:
	$(MAKE) -C ml jitai-sim

ml-jitai-smoke:
	$(MAKE) -C ml jitai-smoke

ml-test:
	$(MAKE) -C ml test

ml-lint:
	$(MAKE) -C ml lint

ml-emotion-smoke:
	$(MAKE) -C ml emotion-smoke

ml-emotion-train:
	$(MAKE) -C ml emotion-train CONFIG=$(CONFIG)

ml-emotion-eval:
	$(MAKE) -C ml emotion-eval ARTIFACT=$(ARTIFACT) DATA=$(DATA)

ml-emotion-tune:
	$(MAKE) -C ml emotion-tune CONFIG=$(CONFIG) TRIALS=$(TRIALS)

ml-emotion-data:
	$(MAKE) -C ml emotion-data SRC=$(SRC) OUT=$(OUT) SCHEME=$(SCHEME) LANG=$(LANG)

# Day 34 compression path (offline smoke; see ml/README.md).
ml-distill-smoke:
	$(MAKE) -C ml distill-smoke

ml-distill-gates:
	$(MAKE) -C ml distill-gates

# Network: downloads GoEmotions (Apache-2.0; see ml/data/CATALOGUE.md).
ml-fetch-goemotions:
	$(MAKE) -C ml fetch-goemotions

ml-mlflow-up:
	$(MAKE) -C ml mlflow-up

ml-mlflow-down:
	$(MAKE) -C ml mlflow-down
