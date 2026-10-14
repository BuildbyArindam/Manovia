"""Day 34 serving: ONNX Runtime behind the existing safety interfaces.

Lazy by construction — importing this package imports no ``onnxruntime``,
``numpy`` or MLflow. The heavy objects load on first use, and every failure
degrades to the pre-Day-34 classifier (ADR 0034).
"""

from __future__ import annotations

from app.services.ml_runtime.featurizer import HashingFeaturizer
from app.services.ml_runtime.registry import (
    ChecksumMismatchError,
    LoadedModel,
    RegistryError,
    RegistryLoader,
)
from app.services.ml_runtime.runtime import (
    ModelRuntimeUnavailableError,
    OnnxModelRuntime,
    RuntimePrediction,
)
from app.services.ml_runtime.shadow import ShadowRecord, ShadowRunner, categorise, summarise

__all__ = [
    "ChecksumMismatchError",
    "HashingFeaturizer",
    "LoadedModel",
    "ModelRuntimeUnavailableError",
    "OnnxModelRuntime",
    "RegistryError",
    "RegistryLoader",
    "RuntimePrediction",
    "ShadowRecord",
    "ShadowRunner",
    "categorise",
    "summarise",
]
