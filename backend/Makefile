.PHONY: dev test lint format

# Development API server (install first: pip install -e ".[dev]")
dev:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

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
