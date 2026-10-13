.PHONY: install data status lock catalogue catalogue-write record train eval report smoke test lint fetch-goemotions mlflow-up mlflow-down emotion-smoke emotion-train emotion-eval emotion-tune emotion-data clean

# ML platform targets (Day 31). Every target is runnable on a laptop with no GPU
# and no network; `fetch-goemotions` is the one exception and says so.
#
#   make install      install the ml package + the ML stack (mlflow, dvc, ...)
#   make data         dvc repro: rebuild the deterministic toy splits
#   make status       is the data what the pipeline recorded? (lock check + dvc status)
#   make lock         only the lock check: does dvc.lock match the code?
#   make catalogue    check ml/data/CATALOGUE.md (licences, verdicts, table)
#   make train        CONFIG=configs/x.yaml   train and log to MLflow
#   make record       CONFIG=configs/x.yaml   re-record the baseline for a config
#   make eval         RUN=<run id>            re-score a logged run
#   make report       RUN=<run id>            write reports/<run id>.md
#   make smoke        the CI path: toy split, tiny train, baseline check, report
#   make test / lint  pytest / ruff + mypy
#
# Day 32 adds the emotion LoRA model (ADR 0032). `emotion-smoke` is the offline
# one: a tiny random-init XLM-R, seconds on a CPU, no network — the same code path
# as a real run. The real runs need a GPU and the corpus; see
# notebooks/colab_emotion_lora.ipynb.
#
#   make emotion-smoke                                  tiny LoRA run, seconds, offline
#   make emotion-train  CONFIG=configs/emotion_lora_xlmr.yaml
#   make emotion-eval   ARTIFACT=artifacts/emotion/manovia-emotion-xlmr-v1
#   make emotion-tune   CONFIG=... TRIALS=12            (optuna.enabled must be true)
#   make emotion-data   SRC=... OUT=... SCHEME=goemotions LANG=en

PYTHON ?= $(shell if [ -x ../.venv/bin/python ]; then echo ../.venv/bin/python; elif [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
PYDIR := $(dir $(abspath $(PYTHON)))

# DVC runs each stage's `cmd` through a shell, so `python` on PATH must be the
# interpreter that has `manovia_ml` installed. Putting the interpreter's own
# directory first makes `make` and `dvc` agree; PYTHONPATH=src covers a checkout
# where the package has not been installed at all (CI installs it properly).
export PATH := $(PYDIR):$(PATH)
export PYTHONPATH := src

# Set before the interpreter starts: `seeds.py` can only *read* PYTHONHASHSEED.
export PYTHONHASHSEED ?= 0

CONFIG ?= configs/toy_emotion_sgd.yaml
EMOTION_CONFIG ?= configs/emotion_lora_smoke.yaml
ARTIFACT ?= artifacts/emotion/manovia-emotion-smoke
DATA ?= data/validation/emotion_validation.jsonl
TRIALS ?= 12
SCHEME ?= goemotions
LANG ?= en
SRC ?=
OUT ?=
RUN ?=
SMOKE_DATE := $(shell date -u +%Y-%m-%d)
SMOKE_REPORT ?= reports/smoke/$(SMOKE_DATE)-smoke.md
SMOKE_JSON ?= /tmp/manovia-ml-smoke-$(SMOKE_DATE).json
EMOTION_JSON ?= /tmp/manovia-ml-emotion-$(SMOKE_DATE).json

install:
	$(PYTHON) -m pip install -e ".[ml,dev]"

data:
	$(PYTHON) -m dvc repro

# Before `dvc repro` rewrites the lock, ask whether the committed lock is honest:
# a dependency whose hash moved means the recorded data was not produced by the
# pipeline in this commit. Missing outputs are fine here — a fresh clone has none.
lock:
	$(PYTHON) -m manovia_ml.data.dvc_lock

status:
	$(PYTHON) -m manovia_ml.data.dvc_lock
	$(PYTHON) -m dvc status

catalogue:
	$(PYTHON) -m manovia_ml.data.catalogue --check

catalogue-write:
	$(PYTHON) -m manovia_ml.data.catalogue --write

train:
	$(PYTHON) -m manovia_ml.training.train --config $(CONFIG)

record:
	$(PYTHON) -m manovia_ml.training.train --config $(CONFIG) --record-expected

eval:
	@test -n "$(RUN)" || { echo "usage: make eval RUN=<run id>"; exit 2; }
	$(PYTHON) -m manovia_ml.evaluation.evaluate --run $(RUN)

report:
	@test -n "$(RUN)" || { echo "usage: make report RUN=<run id>"; exit 2; }
	$(PYTHON) -m manovia_ml.reporting --run $(RUN)

# The full CI path in one command: rebuild the data (so `dvc status` is clean),
# train the toy model, check the committed baseline, write the report. The
# baseline check is the reproducibility gate: the same config + seed must land
# within `evaluation.tolerance` of ml/configs/expected/toy_emotion_sgd.json.
smoke:
	@$(MAKE) --no-print-directory data
	$(PYTHON) -m manovia_ml.data.catalogue --check
	$(PYTHON) -m manovia_ml.training.train --config configs/toy_emotion_sgd.yaml --phase smoke --json > $(SMOKE_JSON)
	$(PYTHON) -c "import json; print('SMOKE RUN_ID=' + json.load(open('$(SMOKE_JSON)'))['run_id'])"
	$(PYTHON) -m manovia_ml.reporting --run $$($(PYTHON) -c "import json;print(json.load(open('$(SMOKE_JSON)'))['run_id'])") --out $(SMOKE_REPORT) --config-path configs/toy_emotion_sgd.yaml
	@echo "--- smoke json ---"
	@cat $(SMOKE_JSON)

emotion-smoke:
	$(PYTHON) -m manovia_ml.training.emotion.train --config $(EMOTION_CONFIG) --json > $(EMOTION_JSON)
	$(PYTHON) -c "import json; d=json.load(open('$(EMOTION_JSON)')); print('EMOTION RUN_ID=' + d['run_id']); print('artifact=' + d['artifact_dir'])"
	$(PYTHON) -m manovia_ml.training.emotion.evaluate --artifact $$($(PYTHON) -c "import json;print(json.load(open('$(EMOTION_JSON)'))['artifact_dir'])") --data $(DATA) --bootstrap 200 --json > /tmp/manovia-ml-emotion-eval.json
	@echo "--- emotion smoke ---"
	@cat $(EMOTION_JSON)

emotion-train:
	$(PYTHON) -m manovia_ml.training.emotion.train --config $(CONFIG)

emotion-eval:
	$(PYTHON) -m manovia_ml.training.emotion.evaluate --artifact $(ARTIFACT) --data $(DATA) --bootstrap 2000 --json

emotion-tune:
	$(PYTHON) -m manovia_ml.training.emotion.tune --config $(CONFIG) --trials $(TRIALS) --json

# Convert a source corpus into the nine-emotion shape (the mapping table).
emotion-data:
	@test -n "$(SRC)" || { echo "usage: make emotion-data SRC=<raw jsonl> OUT=<out jsonl> [SCHEME=goemotions] [LANG=en]"; exit 2; }
	$(PYTHON) -m manovia_ml.training.emotion.data convert $(SRC) --scheme $(SCHEME) --lang $(LANG) --out $(OUT)

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy src tests

# The one target that touches the network: download GoEmotions and convert it.
# See ml/notebooks/README.md for the full Colab/Kaggle sequence.
fetch-goemotions:
	$(PYTHON) -m manovia_ml.data.goemotions --download train --split-name train --out data/raw/goemotions/train.jsonl
	$(PYTHON) -m manovia_ml.data.goemotions --download validation --split-name validation --out data/raw/goemotions/validation.jsonl
	$(PYTHON) -m manovia_ml.data.goemotions --download test --split-name test --out data/raw/goemotions/test.jsonl

# Optional MLflow service (needs Docker). The local file store is the default;
# this is for a shared store or the UI. See docker/mlflow/README.md.
mlflow-up:
	docker compose -f docker/mlflow/docker-compose.mlflow.yml up -d

mlflow-down:
	docker compose -f docker/mlflow/docker-compose.mlflow.yml down

clean:
	rm -rf mlruns artifacts .pytest_cache .mypy_cache .ruff_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
