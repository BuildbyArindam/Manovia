"""Manovia's ML platform (Day 31).

This package is deliberately small and deliberately boring. It exists so that
every model that ever reaches a user can be traced back to:

* a **config** (``ml/configs/*.yaml``, pydantic-validated, no hidden defaults),
* a **dataset version** (a sha256 for every file a run read, plus the DVC stage
  that produced it),
* a **seed** (one integer that reaches every stochastic step),
* a **git commit** (the code that ran, and whether the tree was dirty),
* a **logged run** (MLflow: params, metrics, artefacts, environment), and
* a **card** (``ml/templates/DATASET_CARD.md`` / ``MODEL_CARD.md``) before any
  number from that run is quoted in public.

Read ``ml/README.md`` first, then ``ml/data/CATALOGUE.md``, then
``docs/adr/0031-ml-platform.md``. The ground rules that govern this package are
in ``AGENTS.md`` under "ML ground rules".
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
