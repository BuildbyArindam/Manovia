"""Evaluation: score a logged run on a split it did not train on.

The split is named in the config, and each artefact of a run can be scored
again — that is the whole point of storing the model next to its metrics. The
evaluation run is a *separate* MLflow run (tagged ``manovia.eval_of=<run_id>``),
never a mutation of the training run: a metric that can be edited after the fact
is not evidence.

Honest limit, stated here rather than discovered later: the toy fixture's eval
splits are 24 and 8 rows, so every number from them is a smoke-test number — and
on the fixture the model is at or below the majority-class rate
(``ml/findings/2026-10-13-toy-slice-baselines.md``). Use
``ml/configs/colab_goemotions_logreg.yaml`` (GoEmotions' own test split) before
quoting anything about quality.
"""

from __future__ import annotations

__all__ = ["evaluate", "metrics"]

# Deliberately no eager `from ... import evaluate`: this package's `__init__` runs
# before `python -m manovia_ml.evaluation.evaluate` executes the module, and
# pre-importing it makes runpy warn about "found in sys.modules ... may result in
# unpredictable behaviour" on every `make ml-eval`. The submodules are imported
# explicitly at their use sites.
