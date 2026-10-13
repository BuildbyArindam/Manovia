"""Seeding: stable across processes, independent per step, loud about the environment."""

from __future__ import annotations

import random
import subprocess
import sys

import pytest

from manovia_ml.seeds import derive_seed, seed_everything, seeded_rng


def test_derive_seed_is_stable_across_processes() -> None:
    """`hash()` is randomised per process; sha256 is not — this is the difference."""

    code = "from manovia_ml.seeds import derive_seed;print(derive_seed(7, 'split', 'joy'))"
    first = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    second = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()

    assert first == second
    assert first == str(derive_seed(7, "split", "joy"))


def test_derive_seed_separates_steps_and_parents() -> None:
    assert derive_seed(7, "split") != derive_seed(7, "model")
    assert derive_seed(7, "split") != derive_seed(8, "split")
    # The separator matters: ("a","b") must not collide with ("ab",).
    assert derive_seed(7, "a", "b") != derive_seed(7, "ab")


def test_derive_seed_stays_inside_four_bytes() -> None:
    for parts in ((), ("x",), ("split", "joy", "0")):
        assert 0 <= derive_seed(2**32 - 1, *parts) < 2**32


@pytest.mark.parametrize("seed", [-1, 2**32])
def test_derive_seed_rejects_out_of_range_parents(seed: int) -> None:
    with pytest.raises(ValueError, match="4 bytes"):
        derive_seed(seed)


def test_seed_everything_reports_what_it_did(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "0")

    state = seed_everything(11, pin_blas_threads=1)

    assert state.seed == 11
    assert state.pythonhashseed == "0"
    assert state.blas_threads == 1
    assert state.numpy_seeded is True
    assert state.as_params()["seed"] == "11"
    assert state.as_params()["seed.blas_threads"] == "1"


def test_seed_everything_can_leave_thread_pools_alone() -> None:
    state = seed_everything(1, pin_blas_threads=None)

    assert state.blas_threads is None
    assert state.as_params()["seed.blas_threads"] == "unset"


def test_seed_everything_records_an_unset_hashseed() -> None:
    state = seed_everything(1, pin_blas_threads=None)

    assert state.as_params()["seed.pythonhashseed"] in {"unset", "0", "1"}


def test_seeded_rng_streams_are_reproducible_and_independent() -> None:
    first = [seeded_rng(5, "a").random() for _ in range(3)]
    second = [seeded_rng(5, "a").random() for _ in range(3)]
    other = [seeded_rng(5, "b").random() for _ in range(3)]

    assert first == second
    assert first != other


def test_global_random_state_is_seeded() -> None:
    seed_everything(1234, pin_blas_threads=None)
    first = random.random()
    seed_everything(1234, pin_blas_threads=None)

    assert random.random() == first
