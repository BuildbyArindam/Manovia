"""Shared fixtures.

Everything here keeps the suite **offline and self-contained**: splits are built
from the committed fixture into a temporary directory, MLflow writes to a
temporary file store, and no test depends on `make ml-data` having been run
first. That matters because CI installs the package and runs pytest — if a test
needed a DVC stage to have run, a broken pipeline would look like a broken test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from manovia_ml.config import TrainConfig, load_config_dict
from manovia_ml.data.toy import build_splits


@pytest.fixture
def ml_tracking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the default MLflow store at a temporary directory (never ml/mlruns)."""

    uri = f"file:{tmp_path / 'mlruns'}"
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    return uri


@pytest.fixture
def toy_splits(tmp_path: Path) -> dict[str, dict[str, Any]]:
    """A binary joy/anger split built into a temporary directory."""

    manifest = build_splits(
        out_dir=tmp_path / "processed",
        seed=20261013,
        train_fraction=0.7,
        labels=["joy", "anger"],
        prefix="toy_smoke",
    )
    return {
        "train": {
            "path": manifest.train.path,
            "sha256": manifest.train.sha256,
            "rows": manifest.train.rows,
        },
        "eval": {
            "path": manifest.eval.path,
            "sha256": manifest.eval.sha256,
            "rows": manifest.eval.rows,
        },
    }


def config_dict(
    splits: dict[str, dict[str, Any]],
    *,
    name: str = "test_toy_sgd",
    seed: int = 20261013,
    kind: str = "tfidf_sgd",
    max_iter: int = 15,
    metrics: list[str] | None = None,
    tolerance: float = 1.0e-6,
    experiment: str = "manovia-ml-tests",
) -> dict[str, Any]:
    """A minimal valid config as a dict, with the tmp paths/hashes filled in."""

    model: dict[str, Any] = {
        "kind": kind,
        "tfidf": {
            "word_ngram_range": [1, 2],
            "char_ngram_range": [3, 5],
            "min_df": 1,
            "max_features": 20000,
            "sublinear_tf": True,
            "lowercase": True,
        },
    }
    if kind == "tfidf_sgd":
        model |= {
            "alpha": 1.0e-4,
            "max_iter": max_iter,
            "average": False,
            "class_weight": "balanced",
        }
    else:
        model |= {"C": 2.0, "max_iter": max_iter, "solver": "lbfgs", "class_weight": "balanced"}

    return {
        "name": name,
        "seed": seed,
        "notes": "test config",
        "threads": 1,
        "data": {
            "train": splits["train"],
            "eval": splits["eval"],
            "text_field": "text",
            "label_field": "label",
            "lang_field": "lang",
            "labels": ["joy", "anger"],
        },
        "model": model,
        "evaluation": {
            "tolerance": tolerance,
            "metrics": metrics if metrics is not None else ["accuracy", "macro_f1", "log_loss"],
            "confusion_matrix": True,
        },
        "tracking": {"experiment": experiment, "tracking_uri": None, "log_environment": True},
    }


@pytest.fixture
def toy_config(toy_splits: dict[str, dict[str, Any]], ml_tracking: str) -> TrainConfig:
    return load_config_dict(config_dict(toy_splits), origin="tests/conftest.py")
