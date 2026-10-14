"""Registry loader: production by alias, checksum refusal, previous fallback."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.services.ml_runtime.registry import (
    ChecksumMismatchError,
    RegistryError,
    RegistryLoader,
)


def _version(root: Path, model: str, version: str, payload: bytes = b"model-bytes") -> Path:
    directory = root / model / "versions" / version
    directory.mkdir(parents=True)
    (directory / "model.onnx").write_bytes(payload)
    manifest = {
        "format_version": 1,
        "task": "safety",
        "labels": ["none", "low", "medium", "high", "imminent"],
        "model_file": "model.onnx",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "featurizer": {
            "version": 1,
            "dim": 64,
            "word_ngram_max": 2,
            "char_ngram_min": 3,
            "char_ngram_max": 5,
            "max_length_chars": 2000,
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


def _registry(tmp_path: Path) -> RegistryLoader:
    _version(tmp_path, "safety-student", "v1", b"v1-bytes")
    _version(tmp_path, "safety-student", "v2", b"v2-bytes")
    aliases: dict[str, object] = {"production": "v2", "previous_production": "v1", "staging": None, "archived": []}
    (tmp_path / "safety-student" / "aliases.json").write_text(json.dumps(aliases), encoding="utf-8")
    return RegistryLoader(tmp_path)


def test_loads_production_by_alias(tmp_path: Path) -> None:
    loaded = _registry(tmp_path).load("safety-student")
    assert loaded.version == "v2"
    assert loaded.labels[0] == "none"
    assert loaded.model_path.read_bytes() == b"v2-bytes"


def test_checksum_mismatch_refuses_to_load(tmp_path: Path) -> None:
    loader = _registry(tmp_path)
    model = tmp_path / "safety-student" / "versions" / "v2" / "model.onnx"
    model.write_bytes(b"corrupted-bytes")
    with pytest.raises(ChecksumMismatchError, match="refusing to load"):
        loader.load("safety-student")


def test_fallback_loads_previous_when_production_is_corrupt(tmp_path: Path) -> None:
    loader = _registry(tmp_path)
    model = tmp_path / "safety-student" / "versions" / "v2" / "model.onnx"
    model.write_bytes(b"corrupted-bytes")
    loaded = loader.load_with_fallback("safety-student")
    assert loaded.version == "v1"


def test_missing_alias_is_a_registry_error(tmp_path: Path) -> None:
    loader = _registry(tmp_path)
    with pytest.raises(RegistryError):
        loader.load("safety-student", alias="staging")
