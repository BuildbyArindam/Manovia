"""Regression: numpy must never join the blanket untyped-module overrides.

Day 34's first CI run failed mypy on *pre-existing* code
(``app/services/rag/store.py``) because ``numpy.*`` had been added to the
shared mypy override: with numpy's stubs skipped, every
``x = np.asarray(...)`` lost its inferred type and tripped
``var-annotated`` under CI's Python 3.12, while the sandbox's cache hid
it. numpy ships real stubs and stays fully typed; only the optional
onnx/onnxruntime modules (``serving`` extra) are stubbed out, scoped.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


def _override_modules() -> list[str]:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    modules: list[str] = []
    for override in config["tool"]["mypy"]["overrides"]:
        modules.extend(str(item) for item in override["module"])
    return modules


def test_numpy_is_not_stubbed_out() -> None:
    modules = _override_modules()
    assert not any(item == "numpy" or item.startswith("numpy.") for item in modules), (
        "numpy.* in a follow_imports=skip override breaks typed np.asarray "
        "assignments across the codebase (Day 34 CI failure)"
    )


def test_onnx_overrides_remain_scoped() -> None:
    modules = _override_modules()
    assert "onnxruntime" in modules
    assert "onnx" in modules
