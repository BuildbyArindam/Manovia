.PHONY: dev test coverage-gate lint format migrate migrate-down migrate-check revision ingest eval eval-safety eval-emotion eval-response voice-sim parity parity-casefold parity-corpus parity-output-guard parity-crisis-plans fhir-validate fhir-validator fhir-sample

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

# Day 47 clinician-share bridge: the FHIR document the share endpoint emits.
#
# The *official* HL7 validator is the authority — hapifhir/org.hl7.fhir.core's
# validator_cli.jar, FHIR 4.0.1 — and CI always runs it on the committed sample
# (the `fhir-validation` job in .github/workflows/ci.yml). It needs a JVM and a
# 204 MB download, neither of which every environment has, so `make fhir-validate`
# falls back to scripts/fhir_offline_check.py: a dependency-free structural check
# of the same file, covering the R4 document invariants, reference resolution and
# the "verified terminology or ours" rule. It says out loud which of the two it
# ran, because "validated" means different things in each case.
FHIR_VALIDATOR_VERSION ?= 7.0.2
# The release asset's published sha256 (GitHub reports it on the release; pinned
# here so a tampered download fails before java ever runs).
FHIR_VALIDATOR_SHA256 ?= e3e50f06b806437821b7aef8189a1b790415cb96c5036e0812ed62981452b377
FHIR_VALIDATOR_URL ?= https://github.com/hapifhir/org.hl7.fhir.core/releases/download/$(FHIR_VALIDATOR_VERSION)/validator_cli.jar
FHIR_VALIDATOR_JAR ?= .fhir-validator/validator_cli-$(FHIR_VALIDATOR_VERSION).jar
FHIR_SAMPLE ?= tests/fixtures/fhir/sample_share_bundle.json

# Validate the committed sample: official validator when it is available,
# structural fallback when it is not.
fhir-validate: fhir-sample
	@if command -v java >/dev/null 2>&1 && [ -f "$(FHIR_VALIDATOR_JAR)" ]; then \
		echo "official HL7 validator $(FHIR_VALIDATOR_VERSION) on $(FHIR_SAMPLE)"; \
		java -jar $(FHIR_VALIDATOR_JAR) $(FHIR_SAMPLE) -version 4.0.1 -tx n/a; \
	else \
		echo "official HL7 validator not available here (needs java and $(FHIR_VALIDATOR_JAR);"; \
		echo "run 'make fhir-validator' to fetch it — CI runs it in the fhir-validation job)"; \
		echo "running the offline structural fallback on $(FHIR_SAMPLE)"; \
		$(PYTHON) scripts/fhir_offline_check.py $(FHIR_SAMPLE); \
	fi

# Fetch the official validator once, into a gitignored directory, and verify it.
fhir-validator:
	@mkdir -p $(dir $(FHIR_VALIDATOR_JAR))
	curl -fsSL -o $(FHIR_VALIDATOR_JAR) $(FHIR_VALIDATOR_URL)
	echo "$(FHIR_VALIDATOR_SHA256)  $(FHIR_VALIDATOR_JAR)" | sha256sum -c -

# Fail if the committed sample is not what the current builder produces. A stale
# artifact would mean CI validating a document shape the code no longer emits.
fhir-sample:
	$(PYTHON) scripts/export_fhir_sample.py --check
