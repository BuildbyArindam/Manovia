"""Day 34: distillation, ONNX export, quantisation, gates and registry.

The package is deliberately dependency-light at import time: numpy is the
only hard requirement, ``onnx``/``onnxruntime`` are imported lazily by the
export module, and MLflow by the registry module. That keeps the catalogue
checker, the config tests and CI's pydantic-only consumers working without
the serving stack installed (the Day-31/32 pattern).
"""

from __future__ import annotations
