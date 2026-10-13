"""One place that talks to MLflow, so every run logs the same seven things.

The Day-31 requirement is "every run logs: git commit, data hash, config, seed,
library versions, metrics, artefacts". That sentence is the interface of this
module: :func:`tracked_run` writes the first four plus the environment before the
caller does anything, and hands back a :class:`RunHandle` for metrics and
artefacts.

A run that *cannot* record its provenance should not happen quietly. If git is
unavailable (a tarball, a container without the CLI) the run still starts, but
``git.commit`` is logged as ``unavailable:<reason>`` and ``git.dirty`` is
``true`` — an honest "this cannot be reproduced from a commit" rather than a
blank field someone will later read as "fine".

The store is a local file store by default (``ml/paths.default_tracking_uri``);
``ml/docker/mlflow/docker-compose.mlflow.yml`` runs a server for anyone who
wants the UI or a shared store, and ``MLFLOW_TRACKING_URI`` points at it without
a code change.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from manovia_ml.config import TrainConfig, config_fingerprint
from manovia_ml.hashing import DatasetRef
from manovia_ml.mlflow_api import mlflow
from manovia_ml.paths import default_tracking_uri, ml_root
from manovia_ml.seeds import SeedState

__all__ = ["RunHandle", "git_state", "library_versions", "tracked_run"]

#: Libraries whose version can change a number. Anything else is in the
#: ``environment.json`` artefact; these are also params, so they are visible in
#: the run list without opening a file.
_TRACKED_LIBRARIES = (
    "numpy",
    "scikit-learn",
    "scipy",
    "pandas",
    "joblib",
    "pydantic",
    "pyyaml",
    "mlflow",
    "dvc",
)


def _run_git(*args: str, cwd: Path) -> str | None:
    if shutil.which("git") is None:
        return None
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - environment specific
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def git_state(repo_root: Path | None = None) -> dict[str, str]:
    """Commit, branch and dirty flag for the tree the run is reading code from."""

    root = repo_root or ml_root().parent
    commit = _run_git("rev-parse", "HEAD", cwd=root)
    if commit is None:
        return {
            "commit": "unavailable:not-a-git-checkout",
            "branch": "unavailable",
            "dirty": "true",
        }
    branch = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=root) or "unknown"
    status = _run_git("status", "--porcelain", cwd=root)
    dirty = bool(status)
    return {
        "commit": commit,
        "branch": branch,
        "dirty": str(dirty).lower(),
        "diff_lines": str(len(status.splitlines())) if status else "0",
    }


def library_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in _TRACKED_LIBRARIES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "absent"
    return versions


def _installed_packages() -> dict[str, str]:
    packages: dict[str, str] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"] if dist.metadata else None
        if name:
            packages[name] = dist.version or "unknown"
    return dict(sorted(packages.items()))


@dataclass
class RunHandle:
    """The small, typed surface the rest of the package is allowed to use."""

    run_id: str
    experiment: str
    tracking_uri: str

    def log_params(self, params: Mapping[str, Any]) -> None:
        flat = {str(key): _as_param(value) for key, value in params.items()}
        if flat:
            mlflow.log_params(flat)

    def log_metrics(self, metrics: Mapping[str, float], *, step: int | None = None) -> None:
        for name, value in metrics.items():
            mlflow.log_metric(str(name), float(value), step=step)

    def log_dict(self, payload: Mapping[str, Any], artifact_file: str) -> None:
        mlflow.log_dict(dict(payload), artifact_file)

    def log_artifact(self, path: Path | str, artifact_path: str | None = None) -> None:
        mlflow.log_artifact(str(path), artifact_path=artifact_path)

    def log_text(self, text: str, artifact_file: str) -> None:
        mlflow.log_text(text, artifact_file)

    def set_tags(self, tags: Mapping[str, str]) -> None:
        mlflow.set_tags({str(key): str(value) for key, value in tags.items()})


def _as_param(value: Any) -> str:
    """MLflow params are strings with a 500-character cap; be explicit about it."""

    if isinstance(value, bool):
        text = str(value).lower()
    elif isinstance(value, (list, tuple)):
        text = ",".join(str(item) for item in value)
    elif isinstance(value, dict):
        text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    else:
        text = str(value)
    if len(text) > 500:
        return text[:497] + "..."
    return text


@contextmanager
def tracked_run(
    config: TrainConfig,
    *,
    phase: str,
    data_refs: Sequence[DatasetRef] = (),
    seed_state: SeedState | None = None,
    params: Mapping[str, Any] | None = None,
    tags: Mapping[str, str] | None = None,
    config_path: Path | None = None,
    parent_run_id: str | None = None,
) -> Iterator[RunHandle]:
    """Open an MLflow run that is already self-describing.

    ``phase`` is ``train``, ``eval`` or ``smoke``; it is a tag, so one
    experiment holds the whole history of a config in order.
    """

    tracking_uri = config.tracking.tracking_uri or default_tracking_uri()
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(config.tracking.experiment)

    run_name = config.tracking.run_name or f"{config.name}-{phase}"
    started = datetime.now(UTC).isoformat(timespec="seconds")

    # `nested=True` only when the caller named a parent: MLflow refuses a second
    # active run otherwise, and the eval run is opened while its training run's
    # context is still alive only in tests (the CLI does it in a fresh process).
    with mlflow.start_run(run_name=run_name, nested=parent_run_id is not None) as active:
        run_id = active.info.run_id
        handle = RunHandle(
            run_id=run_id, experiment=config.tracking.experiment, tracking_uri=tracking_uri
        )

        git = git_state()
        handle.set_tags(
            {
                "manovia.phase": phase,
                "manovia.config": config.name,
                "manovia.config.sha256": config_fingerprint(config),
                "manovia.git.commit": git["commit"],
                "manovia.git.branch": git["branch"],
                "manovia.git.dirty": git["dirty"],
                "manovia.started": started,
                **dict(config.tracking.tags),
                **(dict(tags) if tags else {}),
            }
        )
        if parent_run_id:
            mlflow.set_tag("mlflow.parentRunId", parent_run_id)
            handle.set_tags({"manovia.eval_of": parent_run_id})

        handle.log_params(
            {
                "configname": config.name,
                "config.sha256": config_fingerprint(config),
                "config.json": _compact(config.model_dump(mode="json")),
                "phase": phase,
                "git.commit": git["commit"],
                "git.branch": git["branch"],
                "git.dirty": git["dirty"],
                "git.diff_lines": git["diff_lines"],
                "python.version": platform.python_version(),
                "platform": f"{platform.system()}-{platform.machine()}",
                **(seed_state.as_params() if seed_state else {}),
                **{f"lib.{name}": value for name, value in library_versions().items()},
                **(dict(params) if params else {}),
            }
        )

        for ref in data_refs:
            handle.log_params(ref.as_params())

        if config.tracking.log_environment:
            handle.log_dict(
                {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "executable": sys.executable,
                    "libraries": library_versions(),
                    "packages": _installed_packages(),
                    "git": git,
                    "seed": seed_state.as_params() if seed_state else None,
                    "started": started,
                    "ml_root": str(ml_root()),
                },
                "environment.json",
            )

        handle.log_dict(config.model_dump(mode="json"), "config.json")
        if config_path is not None and Path(config_path).is_file():
            handle.log_artifact(Path(config_path), artifact_path="config_source")
        if data_refs:
            handle.log_dict({"datasets": [ref.as_dict() for ref in data_refs]}, "data.json")

        yield handle


def _compact(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
