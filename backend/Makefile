.PHONY: dev test lint format

# Development API server (install first: pip install -e ".[dev]")
# --no-access-log: uvicorn's access log prints the full request line including
# query strings, which may carry user text (AGENTS.md safety rule 5). The app's
# own structlog request line (path only) is emitted by RequestIDMiddleware.
dev:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log

# Test suite with a coverage summary
test:
	pytest --cov=app --cov-report=term-missing

# Lint (ruff check + ruff format check) and type-check (mypy)
lint:
	ruff check .
	ruff format --check .
	mypy app tests

# Apply ruff formatting and safe lint fixes
format:
	ruff format .
	ruff check --fix .
