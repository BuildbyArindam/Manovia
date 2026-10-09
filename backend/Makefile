.PHONY: dev test lint format migrate migrate-down migrate-check revision

# Development API server (install first: pip install -e ".[dev]")
# --no-access-log: uvicorn's access log prints the full request line including
# query strings, which may carry user text (AGENTS.md safety rule 5). The app's
# own structlog request line (path only) is emitted by RequestIDMiddleware.
dev:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log

# Test suite with a coverage summary
test:
	pytest -m "not model" --cov=app --cov-report=term-missing

# Lint (ruff check + ruff format check) and type-check (mypy)
lint:
	ruff check .
	ruff format --check .
	mypy app tests

# Apply ruff formatting and safe lint fixes
format:
	ruff format .
	ruff check --fix .

# Migrations. DATABASE_URL (or backend/.env) decides which database is touched;
# alembic/env.py reads it through app.core.config.Settings.
migrate:
	alembic upgrade head

migrate-down:
	alembic downgrade base

# Fails if a model change has no matching migration (`make lint` stays green
# but CI must not ship drift).
migrate-check:
	alembic check

# Usage: make revision m="add risk index to messages"
revision:
	alembic revision --autogenerate -m "$(m)"
