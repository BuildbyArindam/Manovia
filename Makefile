.PHONY: dev test lint format db-upgrade db-downgrade db-check seed frontend-dev frontend-test frontend-lint frontend-format eval up down smoke

# Backend targets delegate to backend/; frontend targets to frontend/. Docker
# targets (up/down/smoke) drive the compose stack and were wired on Day 7;
# eval remains a placeholder.

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

eval:
	@echo "not implemented yet"
