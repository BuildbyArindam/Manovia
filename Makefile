.PHONY: dev test lint format db-upgrade db-downgrade db-check seed eval up down

# Backend targets are wired to real commands. Frontend targets will be added
# when the frontend scaffold exists; eval/up/down remain placeholders.

dev:
	$(MAKE) -C backend dev

test:
	$(MAKE) -C backend test

lint:
	$(MAKE) -C backend lint

format:
	$(MAKE) -C backend format

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

eval up down:
	@echo "not implemented yet"
