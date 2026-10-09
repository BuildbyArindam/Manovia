.PHONY: dev test lint format db-upgrade db-downgrade db-check seed frontend-dev frontend-test frontend-lint frontend-format eval up down

# Backend targets delegate to backend/; frontend targets to frontend/. The
# frontend scaffold exists since Day 5, so both halves are wired to real
# commands here. eval/up/down remain placeholders.

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

eval up down:
	@echo "not implemented yet"
