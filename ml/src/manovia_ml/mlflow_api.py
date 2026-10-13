"""The one place this package imports MLflow.

MLflow 3.x refuses to open a filesystem tracking store unless
``MLFLOW_ALLOW_FILE_STORE`` is set, because the file store is in maintenance mode
upstream (new server-side features land in the database backends only). A local
file store is still the right default for Manovia: one machine writing one record
per run, no server process, no database to back up, artefacts readable with
``ls``. The opt-out is therefore set here, before the import, with ``setdefault``
so an operator keeps control — ``MLFLOW_ALLOW_FILE_STORE=false`` restores the
exception, and pointing ``tracking_uri`` at SQLite or a server makes this
irrelevant.

Why a module instead of a line in each file: the flag has to be set before *any*
MLflow store is constructed, in every process that touches a run record. It used
to live in ``tracking.py``, which meant the reporting step of the Day-31 smoke
run — a separate process that never imports ``tracking`` — trained fine and then
died with the maintenance-mode error while building its client. One owner, one
import, no ordering puzzles in the callers (they import names from here like any
other dependency).
"""

from __future__ import annotations

import os

__all__ = ["MlflowClient", "allow_local_file_store", "mlflow"]

#: Set before the import below; see the module docstring.
allow_local_file_store = os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

import mlflow  # noqa: E402  (must come after the opt-out above)
from mlflow.tracking import MlflowClient  # noqa: E402
