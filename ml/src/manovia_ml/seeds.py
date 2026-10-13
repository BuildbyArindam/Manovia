"""Deterministic seeding.

One integer in the config must reach *every* stochastic step, and each step must
get its own stream so that adding a step does not silently change the stream
another step sees. That is what :func:`derive_seed` is for: sub-seeds are derived
with sha256 of a stable label, not by adding 1 to the parent.

Determinism has three separate parts, and only the first one is the seed:

1. **Seeds** — Python's ``random``, NumPy's generators, everything sklearn
   (which takes ``random_state``) and, when torch is installed, torch's global
   RNG are seeded from the config. Torch matters for the Day-32 emotion runs:
   the classification head and the LoRA matrices are *initialised* from that RNG,
   so leaving it unseeded made two identical-config runs disagree on ECE while
   agreeing on every thresholded metric — the worst kind of flake.
2. **Thread counts** — BLAS reductions are not associative, so the *same* seed
   with a different number of OpenMP threads can produce last-bit differences.
   ``seed_everything`` pins the pools to 1 thread by default.
3. **Environment** — ``PYTHONHASHSEED`` is read at interpreter start, so it
   cannot be set from inside a run; we record it instead, and the Makefile sets
   it for the smoke run. Iterating a *set* of strings anywhere in a pipeline is
   a bug for this reason; use sorted lists (this package does).

Nothing here is memory-hard or secret: these are reproducibility seeds, not
cryptographic keys.
"""

from __future__ import annotations

import hashlib
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["SeedState", "derive_seed", "seed_everything", "seeded_rng"]

#: Recorded in every run so a reader can see whether hash randomisation was on.
ENV_PYTHONHASHSEED = "PYTHONHASHSEED"


@dataclass(frozen=True)
class SeedState:
    """What seeding actually did, for logging and for tests to assert on."""

    seed: int
    pythonhashseed: str | None
    blas_threads: int | None
    numpy_seeded: bool
    torch_seeded: bool = False

    def as_params(self) -> dict[str, str]:
        return {
            "seed": str(self.seed),
            "seed.pythonhashseed": self.pythonhashseed or "unset",
            "seed.blas_threads": "unset" if self.blas_threads is None else str(self.blas_threads),
            "seed.numpy_seeded": str(self.numpy_seeded).lower(),
            "seed.torch_seeded": str(self.torch_seeded).lower(),
        }


def derive_seed(seed: int, *parts: str) -> int:
    """Derive a stable sub-seed for a named step.

    ``derive_seed(7, "split") != derive_seed(7, "model")``, and both are stable
    across processes, machines and Python versions (sha256 of the joined parts,
    first 4 bytes, not Python's ``hash()``, which is randomised per process).
    """

    if not 0 <= seed < 2**32:
        raise ValueError(f"seed must fit in 4 bytes, got {seed}")
    payload = "\x1f".join([str(seed), *parts]).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:4], "big")


def seed_everything(seed: int, *, pin_blas_threads: int | None = 1) -> SeedState:
    """Seed Python, NumPy and the BLAS thread pools; return what was done.

    ``pin_blas_threads=1`` is the default because it is the only setting that
    makes float results identical between two machines with different core
    counts. Pass ``None`` to leave the pools alone (faster, slightly less
    reproducible — the config's tolerance then covers it).
    """

    random.seed(seed)

    numpy_seeded = False
    try:  # numpy is in the `ml` extra; keep this importable without it.
        import numpy as np

        np.random.seed(seed)  # legacy global state, still used by some libs
        numpy_seeded = True
    except ImportError:  # pragma: no cover - exercised by the no-numpy test
        pass

    torch_seeded = False
    try:  # torch is in the `training` extra; the linear path must not need it.
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        torch_seeded = True
    except ImportError:  # pragma: no cover - exercised by the no-torch test
        pass

    applied_threads: int | None = None
    if pin_blas_threads is not None:
        applied_threads = _pin_threads(pin_blas_threads)

    return SeedState(
        seed=seed,
        pythonhashseed=os.environ.get(ENV_PYTHONHASHSEED),
        blas_threads=applied_threads,
        numpy_seeded=numpy_seeded,
        torch_seeded=torch_seeded,
    )


def _pin_threads(limit: int) -> int | None:
    """Limit OpenMP/BLAS pools for the rest of the process.

    ``threadpoolctl`` (a scikit-learn dependency) is the only reliable way to do
    this *after* the libraries are already imported: ``OMP_NUM_THREADS`` is read
    when the native library loads, which may already have happened.
    """

    try:
        from threadpoolctl import threadpool_limits
    except ImportError:  # pragma: no cover - threadpoolctl ships with scikit-learn
        return None
    # threadpool_limits is a context manager; entering it without exiting is
    # deliberate here — the limit applies to the whole process on purpose.
    # Entering without exiting is deliberate: the limit is process-wide.
    threadpool_limits(limits=limit).__enter__()
    return limit


def seeded_rng(seed: int, *parts: str) -> random.Random:
    """A ``random.Random`` whose stream is derived from ``seed`` and ``parts``."""

    return random.Random(derive_seed(seed, *parts))


def describe_environment() -> dict[str, Any]:  # pragma: no cover - thin wrapper
    """Small odds and ends that explain a run but are not model parameters."""

    return {
        "pythonhashseed": os.environ.get(ENV_PYTHONHASHSEED, "unset"),
        "cwd": str(Path.cwd()),
    }
