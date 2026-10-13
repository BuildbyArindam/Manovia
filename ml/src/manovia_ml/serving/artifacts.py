"""The artefact bundle: one file that holds a model and everything about it.

Format (``joblib``, a dict — deliberately dull):

.. code-block:: python

    {
        "format": "manovia-ml-bundle",
        "format_version": 1,
        "pipeline": <sklearn Pipeline>,
        "labels": ["anger", "calm", ...],       # explicit, ordered
        "meta": {...},                          # config fingerprint, run id, versions
    }

Three decisions worth stating:

* **The label order travels with the model.** Deriving it from the classifier at
  load time works right up until someone re-orders a dataset, and then every
  predicted label shifts by one without an error.
* **The library versions travel with the model.** joblib will happily unpickle a
  model built by another scikit-learn into subtly different coefficients
  (the ``InconsistentVersionWarning`` this repository has already met once).
  Here that is a hard error unless a caller opts in.
* **No pickle of anything but sklearn/numpy.** joblib.load executes code; the
  bundle is only ever loaded from a store this project wrote to. Serving a
  bundle from an untrusted source is not supported and is not a feature to add.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

import joblib

from manovia_ml.mlflow_api import mlflow
from manovia_ml.paths import ml_root

__all__ = [
    "BUNDLE_FILENAME",
    "BUNDLE_FORMAT",
    "BUNDLE_FORMAT_VERSION",
    "Bundle",
    "BundleError",
    "fetch_run_artifact",
    "load_bundle",
    "save_bundle",
]

BUNDLE_FORMAT = "manovia-ml-bundle"
BUNDLE_FORMAT_VERSION = 1
BUNDLE_FILENAME = "model.joblib"

_SKLEARN = "scikit-learn"


class BundleError(RuntimeError):
    """The bundle is missing, malformed, or was built with a different stack."""


@dataclass
class Bundle:
    """A loaded model and the facts needed to talk about it honestly."""

    pipeline: Any
    labels: list[str]
    meta: dict[str, Any]

    def predict(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        predicted = self.pipeline.predict(texts)
        return [str(label) for label in predicted]

    def predict_proba(self, texts: list[str]) -> list[dict[str, float]]:
        """Probabilities keyed by the bundle's label order.

        The columns come back in the *estimator's* class order, which is not the
        bundle's label order (it is lexicographic, and a config's label order is
        a deliberate choice). Reading them positionally would silently mislabel
        every probability — the kind of bug that survives a code review and dies
        in production. So: map through `classes_`, then key by the bundle's
        labels, and drop nothing silently.
        """

        if not texts:
            return []
        if not hasattr(self.pipeline, "predict_proba"):
            raise BundleError("this model has no predict_proba (was it trained with average=True?)")
        classes = [str(label) for label in self.pipeline.classes_]
        unknown = [label for label in classes if label not in self.labels]
        if unknown:
            raise BundleError(f"model predicts labels the bundle does not list: {unknown}")
        probabilities = self.pipeline.predict_proba(texts)
        return [
            {
                label: float(row[classes.index(label)]) if label in classes else 0.0
                for label in self.labels
            }
            for row in probabilities
        ]

    @property
    def run_id(self) -> str | None:
        value = self.meta.get("run_id")
        return str(value) if value else None

    @property
    def config_sha256(self) -> str | None:
        value = self.meta.get("config_sha256")
        return str(value) if value else None


def library_versions() -> dict[str, str]:
    versions: dict[str, str] = {"python": platform.python_version()}
    for name in (_SKLEARN, "numpy", "joblib"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:  # pragma: no cover - broken environment
            versions[name] = "absent"
    return versions


def save_bundle(
    path: Path | str,
    *,
    pipeline: Any,
    labels: list[str],
    meta: dict[str, Any],
) -> Path:
    """Write the bundle and return where it went."""

    if not labels:
        raise BundleError("refusing to save a bundle with no labels")
    payload = {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_FORMAT_VERSION,
        "pipeline": pipeline,
        "labels": list(labels),
        "meta": {**meta, "library_versions": library_versions()},
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, destination, compress=3)
    return destination


def load_bundle(path: Path | str, *, allow_version_mismatch: bool = False) -> Bundle:
    source = Path(path)
    if not source.is_file():
        raise BundleError(f"no bundle at {source}")
    payload = joblib.load(source)
    if not isinstance(payload, dict) or payload.get("format") != BUNDLE_FORMAT:
        raise BundleError(f"{source}: not a {BUNDLE_FORMAT} bundle")
    if payload.get("format_version") != BUNDLE_FORMAT_VERSION:
        raise BundleError(
            f"{source}: bundle format {payload.get('format_version')!r} is not "
            f"{BUNDLE_FORMAT_VERSION!r}"
        )
    labels = payload.get("labels")
    if not isinstance(labels, list) or not labels:
        raise BundleError(f"{source}: bundle has no labels")
    meta = payload.get("meta") or {}
    saved_versions = meta.get("library_versions") or {}
    current = library_versions()
    mismatches = {
        name: (saved_versions.get(name), version)
        for name, version in current.items()
        if name in saved_versions and saved_versions[name] != version
    }
    if mismatches and not allow_version_mismatch:
        detail = ", ".join(f"{name} {saved}→{now}" for name, (saved, now) in mismatches.items())
        raise BundleError(
            f"{source}: built with a different stack ({detail}); pass "
            "allow_version_mismatch=True only to look at a model, never to quote it"
        )
    return Bundle(pipeline=payload["pipeline"], labels=[str(label) for label in labels], meta=meta)


def fetch_run_artifact(
    run_id: str,
    *,
    artifact_path: str = "model",
    tracking_uri: str | None = None,
    destination: Path | str | None = None,
) -> Path:
    """Download a run's artefact directory (local store: a copy, server: a download)."""

    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    target = Path(destination) if destination is not None else ml_root() / "artifacts" / "downloads"
    downloaded = mlflow.artifacts.download_artifacts(
        run_id=run_id, artifact_path=artifact_path, dst_path=str(target)
    )
    resolved = Path(str(downloaded))
    candidate = resolved / BUNDLE_FILENAME if resolved.is_dir() else resolved
    if not candidate.is_file():
        raise BundleError(f"run {run_id}: no {BUNDLE_FILENAME} under {artifact_path!r}")
    return candidate
