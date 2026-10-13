"""Where things live, resolved once and the same way everywhere.

Every path in this package is either (a) explicit in a config, or (b) resolved
here. Nothing reads the process's working directory implicitly, because a
training run that depends on where you happened to stand when you started it is
not reproducible.

The ML project root is the directory that holds ``ml/pyproject.toml``. It is
found, in order:

1. ``MANOVIA_ML_ROOT`` — an explicit override, used by tests and CI if the
   checkout is somewhere unusual;
2. the installed package's own location (``<root>/ml/src/manovia_ml/__init__.py``
   gives ``<root>/ml`` even for a plain, non-editable install);
3. by walking up from the working directory (the case of "I cloned this and ran
   the Makefile").

If none of them applies we raise rather than silently inventing a directory:
a run that writes its artefacts into a temporary directory is worse than a run
that refuses to start.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_ML_ROOT = "MANOVIA_ML_ROOT"
ENV_TRACKING_URI = "MLFLOW_TRACKING_URI"

#: Directory (relative to the ML root) holding the default file-store tracking
#: data. Git-ignored; see ``ml/.gitignore``.
DEFAULT_TRACKING_DIRNAME = "mlruns"

#: Relative paths inside the ML project that other modules build on.
CONFIG_DIRNAME = "configs"
DATA_DIRNAME = "data"
REPORTS_DIRNAME = "reports"
TEMPLATES_DIRNAME = "templates"


class MLRootNotFound(RuntimeError):
    """Raised when the ML project root cannot be determined."""


def _looks_like_ml_root(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() and (path / "src" / "manovia_ml").is_dir()


def ml_root() -> Path:
    """Return the ML project root (the directory containing ``ml/pyproject.toml``)."""

    override = os.environ.get(ENV_ML_ROOT, "").strip()
    if override:
        candidate = Path(override).expanduser().resolve()
        if not candidate.is_dir():
            raise MLRootNotFound(f"{ENV_ML_ROOT}={override!r} is not a directory")
        return candidate

    # <root>/ml/src/manovia_ml/paths.py -> parents: [manovia_ml, src, ml]
    installed = Path(__file__).resolve().parents[2]
    if _looks_like_ml_root(installed):
        return installed

    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if _looks_like_ml_root(candidate):
            return candidate

    raise MLRootNotFound(
        "could not find the ML project root; run from inside the repository or set "
        f"{ENV_ML_ROOT} to the directory containing ml/pyproject.toml"
    )


def resolve(path: str | Path) -> Path:
    """Resolve ``path`` relative to the ML root (absolute paths pass through)."""

    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (ml_root() / candidate).resolve()


def config_dir() -> Path:
    return ml_root() / CONFIG_DIRNAME


def data_dir() -> Path:
    return ml_root() / DATA_DIRNAME


def reports_dir() -> Path:
    return ml_root() / REPORTS_DIRNAME


def templates_dir() -> Path:
    return ml_root() / TEMPLATES_DIRNAME


def default_tracking_uri() -> str:
    """The MLflow tracking URI used when a config does not name one.

    Order: the config, then ``MLFLOW_TRACKING_URI``, then a local file store at
    ``<ml root>/mlruns``. A file store is the default on purpose — MLflow's own
    default is an implicit ``./mlruns`` relative to the working directory, which
    is the opposite of reproducible. ``ml/docker/mlflow/docker-compose.mlflow.yml``
    runs the same thing as a service for anyone who wants the UI shared.
    """

    env = os.environ.get(ENV_TRACKING_URI, "").strip()
    if env:
        return env
    return f"file:{ml_root() / DEFAULT_TRACKING_DIRNAME}"
