"""ONNX Runtime serving: batching, warm-up, timeouts, previous-version fallback.

Design rules, inherited from the rest of the safety stack:

* **Never fatal.** A missing ``onnxruntime`` install, a missing artefact or
  a failed session construction raises :class:`ModelRuntimeUnavailableError`;
  the caller (``classifier.build_onnx_safety_classifier``) turns that into
  the pre-Day-34 classifier. Deleting the weights is a supported state.
* **Lazy imports.** ``onnxruntime``/``numpy`` are imported inside methods —
  this module must import in a deployment without the serving extra.
* **Bounded work.** Inference runs on a owned thread pool. ``predict``
  waits at most ``timeout_seconds``; a timeout tries the previous version
  once, then gives up — a stalled model must never stall a crisis reply.
* **Dynamic batching.** Concurrent single predictions are coalesced by a
  batcher thread into one ``session.run`` up to ``max_batch_size`` or
  ``batch_wait_ms``, whichever comes first. Batching is an optimisation,
  never a correctness input: each caller gets exactly its own row back.
* **Warm-up on startup.** :meth:`OnnxModelRuntime.warmup` runs synthetic
  inputs through every loaded session so the first real message does not
  pay session/JIT warm-up. It is called from app startup when the runtime
  is enabled, and its failure is recorded, not raised.

Privacy: this module receives text only to featurize it in-process. It
stores nothing, logs nothing (categories and timings travel in return
values), and never sends text anywhere.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.ml_runtime.featurizer import HashingFeaturizer
from app.services.ml_runtime.registry import LoadedModel

__all__ = [
    "ModelRuntimeUnavailableError",
    "OnnxModelRuntime",
    "RuntimePrediction",
]


class ModelRuntimeUnavailableError(RuntimeError):
    """No session could answer (missing extra, bad artefact, timeout)."""


@dataclass(frozen=True)
class RuntimePrediction:
    """Probabilities for one message plus serving metadata (never text)."""

    probabilities: tuple[float, ...]
    model_version: str
    latency_ms: float
    used_fallback: bool = False


@dataclass
class _Session:
    """One loaded ONNX session and everything needed to feed it."""

    loaded: LoadedModel
    featurizer: HashingFeaturizer
    session: Any  # onnxruntime.InferenceSession, typed Any: optional extra
    input_name: str


@dataclass
class _BatchItem:
    text: str
    future: Future[tuple[float, ...]] = field(default_factory=Future)


class _Batcher:
    """Coalesces concurrent singles into batched ``session.run`` calls."""

    def __init__(
        self,
        session: _Session,
        *,
        max_batch_size: int,
        batch_wait_ms: float,
        executor: ThreadPoolExecutor,
    ) -> None:
        self._session = session
        self._max_batch_size = max_batch_size
        self._wait_seconds = batch_wait_ms / 1000.0
        self._executor = executor
        self._queue: queue.SimpleQueue[_BatchItem] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, name="ml-runtime-batcher", daemon=True)
        self._thread.start()

    def submit(self, text: str) -> Future[tuple[float, ...]]:
        item = _BatchItem(text=text)
        self._queue.put(item)
        return item.future

    def _run(self) -> None:
        while True:
            first = self._queue.get()
            batch = [first]
            deadline = time.monotonic() + self._wait_seconds
            while len(batch) < self._max_batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(self._queue.get(timeout=remaining))
                except queue.Empty:
                    break
            self._executor.submit(self._execute, batch)

    def _execute(self, batch: list[_BatchItem]) -> None:
        try:
            features = self._session.featurizer.transform([item.text for item in batch])
            rows = self._run_session(features)
        except Exception as exc:  # every caller degrades the same way
            for item in batch:
                if not item.future.done():
                    item.future.set_exception(exc)
            return
        for item, row in zip(batch, rows, strict=True):
            if not item.future.done():
                item.future.set_result(tuple(float(value) for value in row))

    def _run_session(self, features: list[list[float]]) -> Any:
        import numpy as np

        array = np.asarray(features, dtype=np.float32)
        outputs = self._session.session.run(["probabilities"], {self._session.input_name: array})
        return outputs[0]


class OnnxModelRuntime:
    """Production session + previous-version fallback, behind one predict."""

    def __init__(
        self,
        primary: LoadedModel,
        *,
        fallback: LoadedModel | None = None,
        timeout_seconds: float = 2.0,
        max_batch_size: int = 16,
        batch_wait_ms: float = 5.0,
        max_workers: int = 4,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be at least 1")
        self._primary_spec = primary
        self._fallback_spec = fallback
        self._timeout = timeout_seconds
        self._max_batch_size = max_batch_size
        self._batch_wait_ms = batch_wait_ms
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="ml-runtime"
        )
        self._sessions: dict[str, _Session] = {}
        self._batchers: dict[str, _Batcher] = {}
        self._lock = threading.Lock()
        self._failure: str = "none"
        self._warmed = False

    # -- loading -------------------------------------------------------------

    @property
    def failure(self) -> str:
        return self._failure

    @property
    def version(self) -> str:
        return self._primary_spec.version

    @property
    def warmed(self) -> bool:
        return self._warmed

    def _load_session(self, spec: LoadedModel) -> _Session:
        cached = self._sessions.get(spec.version)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._sessions.get(spec.version)
            if cached is not None:
                return cached
            try:
                import onnxruntime as ort
            except ImportError as exc:
                self._failure = "onnxruntime_missing"
                raise ModelRuntimeUnavailableError(
                    "onnxruntime is not installed (serving extra)"
                ) from exc
            featurizer_spec = spec.manifest.get("featurizer")
            if not isinstance(featurizer_spec, dict):
                self._failure = "manifest_missing_featurizer"
                raise ModelRuntimeUnavailableError("manifest lacks a featurizer spec")
            try:
                featurizer = HashingFeaturizer.from_spec(
                    {str(k): int(v) for k, v in featurizer_spec.items()}
                )
                options = ort.SessionOptions()
                options.intra_op_num_threads = 1
                session = ort.InferenceSession(
                    str(spec.model_path), sess_options=options, providers=["CPUExecutionProvider"]
                )
            except Exception as exc:
                self._failure = f"session_failed:{type(exc).__name__}"
                raise ModelRuntimeUnavailableError(
                    f"could not load ONNX model {spec.model_name}@{spec.version}"
                ) from exc
            loaded = _Session(
                loaded=spec,
                featurizer=featurizer,
                session=session,
                input_name=session.get_inputs()[0].name,
            )
            self._sessions[spec.version] = loaded
            self._batchers[spec.version] = _Batcher(
                loaded,
                max_batch_size=self._max_batch_size,
                batch_wait_ms=self._batch_wait_ms,
                executor=self._executor,
            )
            return loaded

    def warmup(self, *, texts: Sequence[str] = ("warm up", "the day feels long")) -> bool:
        """Run synthetic inputs through primary (and fallback) sessions.

        Returns True when the primary answered. Failure is recorded in
        ``failure`` and reported by the return value — startup must not die
        because a model would not warm (ADR 0034).
        """

        try:
            session = self._load_session(self._primary_spec)
            for text in texts:
                session.featurizer.transform_one(text)
            self._batchers[session.loaded.version].submit(texts[0]).result(timeout=self._timeout)
        except Exception:
            if self._failure == "none":
                self._failure = "warmup_failed"
            return False
        if self._fallback_spec is not None:
            # Fallback warmth is best-effort; its load is retried live.
            with suppress(ModelRuntimeUnavailableError):
                self._load_session(self._fallback_spec)
        self._warmed = True
        return True

    # -- inference -----------------------------------------------------------

    def _predict_with(self, spec: LoadedModel, text: str) -> tuple[float, ...]:
        self._load_session(spec)  # ensures the batcher for this version exists
        future = self._batchers[spec.version].submit(text)
        try:
            return future.result(timeout=self._timeout)
        except FutureTimeout as exc:
            raise ModelRuntimeUnavailableError(
                f"model {spec.model_name}@{spec.version} timed out after {self._timeout}s"
            ) from exc

    def predict(self, text: str) -> RuntimePrediction:
        """Probabilities for one message; previous version on any failure."""

        started = time.perf_counter()
        try:
            probabilities = self._predict_with(self._primary_spec, text)
            return RuntimePrediction(
                probabilities=probabilities,
                model_version=self._primary_spec.version,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        except ModelRuntimeUnavailableError:
            if self._fallback_spec is None:
                raise
        fallback = self._fallback_spec
        assert fallback is not None  # narrowed by the raise above
        probabilities = self._predict_with(fallback, text)
        return RuntimePrediction(
            probabilities=probabilities,
            model_version=fallback.version,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            used_fallback=True,
        )

    def predict_many(self, texts: Sequence[str]) -> list[RuntimePrediction]:
        """Batched inference without the batcher queue (eval/load paths)."""

        if not texts:
            return []
        started = time.perf_counter()
        session = self._load_session(self._primary_spec)
        batcher = self._batchers[session.loaded.version]
        rows = batcher._run_session(session.featurizer.transform(texts))
        latency = (time.perf_counter() - started) * 1000.0
        return [
            RuntimePrediction(
                probabilities=tuple(float(value) for value in row),
                model_version=self._primary_spec.version,
                latency_ms=latency / len(texts),
            )
            for row in rows
        ]

    def describe(self) -> dict[str, object]:
        """Serving metadata for logs/dev endpoints: versions, never text."""

        return {
            "primary_version": self._primary_spec.version,
            "fallback_version": self._fallback_spec.version if self._fallback_spec else None,
            "loaded_versions": sorted(self._sessions),
            "warmed": self._warmed,
            "failure": self._failure,
            "timeout_seconds": self._timeout,
            "max_batch_size": self._max_batch_size,
        }

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def build_runtime_from_registry(
    registry_dir: str | Path,
    model_name: str,
    *,
    timeout_seconds: float = 2.0,
    max_batch_size: int = 16,
    batch_wait_ms: float = 5.0,
    max_workers: int = 4,
) -> OnnxModelRuntime:
    """Production (checksum-verified) + previous version as the fallback rung."""

    from app.services.ml_runtime.registry import RegistryLoader

    loader = RegistryLoader(registry_dir)
    primary = loader.load(model_name, alias="production")
    fallback: LoadedModel | None = None
    aliases = loader.aliases(model_name)
    if isinstance(aliases.get("previous_production"), str):
        try:
            fallback = loader.load(model_name, alias="previous_production")
        except Exception:
            fallback = None  # a broken previous version is no fallback at all
    return OnnxModelRuntime(
        primary,
        fallback=fallback,
        timeout_seconds=timeout_seconds,
        max_batch_size=max_batch_size,
        batch_wait_ms=batch_wait_ms,
        max_workers=max_workers,
    )
