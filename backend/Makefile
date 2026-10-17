.PHONY: dev test coverage-gate lint format migrate migrate-down migrate-check revision ingest eval eval-safety eval-emotion eval-response voice-sim parity parity-casefold parity-corpus parity-output-guard parity-crisis-plans

PYTHON ?= $(shell if [ -x ../.venv/bin/python ]; then echo ../.venv/bin/python; elif [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python; fi)

# Development API server (install first: pip install -e ".[dev]")
# --no-access-log: uvicorn's access log prints the full request line including
# query strings, which may carry user text (AGENTS.md safety rule 5). The app's
# own structlog request line (path only) is emitted by RequestIDMiddleware.
dev:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log

# Test suite with a coverage summary, then the Day 26 coverage gates
# (scripts/coverage_gate.py): backend overall >= 85%, every app/services/safety
# module >= 95%, crypto and scoring 100% — statements *and* branches. The gate
# runs as part of `make test` rather than as a separate target so a red number
# cannot be merged by forgetting to call it.
COVERAGE_JSON ?= coverage.json
test:
	pytest --cov=app --cov-branch --cov-report=term-missing --cov-report=json:$(COVERAGE_JSON)
	$(PYTHON) scripts/coverage_gate.py $(COVERAGE_JSON)

# The gate on its own, against the report the last `make test` wrote.
coverage-gate:
	$(PYTHON) scripts/coverage_gate.py $(COVERAGE_JSON)

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

# Day 24 unified evaluation harness: runs safety (dev split), emotion (162
# multilingual cases), and response pipeline (60 scripted scenarios), writes
# evals/reports/<date>/summary.md + report.html + pr_comment.md, and enforces
# the CI gates in evals/thresholds.yaml.
#
# Day 27: the optional LLM judge is behind its feature flag. The deterministic
# gates always run; pass EVALS_LLM_JUDGE=1 (or export FEATURE_LLM_JUDGE=true)
# to additionally score warmth/validation/non_directive/safety with the
# advisory-only judge.
SPLIT ?= dev
EVAL_PROVIDER ?= fake
EVALS_LLM_JUDGE ?= 0
EVAL_JUDGE_ARG = $(if $(filter 1,$(EVALS_LLM_JUDGE)),--with-judge,)
eval:
	LLM_PROVIDER=$(EVAL_PROVIDER) $(PYTHON) ../evals/run_all_evals.py --provider $(EVAL_PROVIDER) $(EVAL_JUDGE_ARG)

eval-safety:
	$(PYTHON) ../evals/run_safety_eval.py --split $(SPLIT)

eval-emotion:
	$(PYTHON) ../evals/run_emotion_eval.py

eval-response:
	LLM_PROVIDER=$(EVAL_PROVIDER) $(PYTHON) ../evals/run_response_eval.py --provider $(EVAL_PROVIDER) $(EVAL_JUDGE_ARG)

# Day 44: the agentic navigator's deterministic eval —
# 40 scripted scenarios against the real loop with a
# scripted fake model (offline).
eval-agent:
	$(PYTHON) ../evals/agent/run_agent_eval.py

# Day 40: deterministic fake-stage timing; no microphone, audio bytes, model
# download or network call. Prints per-stage p50/p95 and the <1.5s gate.
voice-sim:
	$(PYTHON) scripts/simulate_voice.py

# Day 13 knowledge ingestion: chunk + embed + store the curated corpus,
# idempotently (a second run is a no-op). RAG_EMBEDDER picks the embedder
# (auto = sentence-transformers when installed, tfidf offline); settings come
# from the environment or backend/.env like every other knob.
ingest:
	python -m app.services.rag.ingest

# Day 41 cross-language parity. `parity` verifies; the other two regenerate one
# stage each, because the stages depend on each other across the two trees (see
# the root Makefile's parity-run, which orders them).
parity:
	$(PYTHON) ../evals/parity/build_corpus.py --check
	$(PYTHON) ../evals/parity/export_python_expected.py --check
	$(PYTHON) ../evals/parity/export_normalisation.py --check
	$(PYTHON) ../evals/parity/export_output_guard.py --check
	$(PYTHON) ../evals/parity/export_crisis_plans.py --check
	$(PYTHON) scripts/export_casefold_cases.py --check
	$(PYTHON) -m pytest tests/safety/test_private_bundle.py -q

parity-output-guard:
	$(PYTHON) ../evals/parity/export_output_guard.py

parity-crisis-plans:
	$(PYTHON) ../evals/parity/export_crisis_plans.py

parity-casefold:
	$(PYTHON) scripts/export_casefold_cases.py

parity-corpus:
	$(PYTHON) ../evals/parity/build_corpus.py
	$(PYTHON) ../evals/parity/export_python_expected.py
	$(PYTHON) ../evals/parity/export_normalisation.py
