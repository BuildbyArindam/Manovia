.PHONY: dev test lint format db-upgrade db-downgrade db-check seed ingest retention frontend-dev frontend-test frontend-lint frontend-format eval up down smoke prod-up prod-down prod-migrate ml-install ml-data ml-status ml-catalogue ml-train ml-record ml-eval ml-report ml-smoke ml-test ml-lint ml-fetch-goemotions ml-mlflow-up ml-mlflow-down

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
#   make ml-data  (= dvc repro)      make ml-status (= dvc status)
#   make ml-catalogue                                 licence + verdict check
#   make ml-install                                   pip install -e "./ml[ml,dev]"
#   make ml-test  /  make ml-lint                     pytest / ruff + mypy
ml-install:
	$(MAKE) -C ml install

ml-data:
	$(MAKE) -C ml data

ml-status:
	$(MAKE) -C ml status

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

ml-test:
	$(MAKE) -C ml test

ml-lint:
	$(MAKE) -C ml lint

# Network: downloads GoEmotions (Apache-2.0; see ml/data/CATALOGUE.md).
ml-fetch-goemotions:
	$(MAKE) -C ml fetch-goemotions

ml-mlflow-up:
	$(MAKE) -C ml mlflow-up

ml-mlflow-down:
	$(MAKE) -C ml mlflow-down
