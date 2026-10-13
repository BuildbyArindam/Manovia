"""Data handling: the toy fixture, deterministic splits, and the catalogue.

Nothing in this package downloads anything. Real corpora are fetched by the
documented steps in ``ml/data/CATALOGUE.md`` and versioned with DVC; the tests
and the smoke run use the hand-written fixture in ``ml/data/toy/`` so that the
suite stays offline (AGENTS.md, ML ground rule 10).
"""

from __future__ import annotations

__all__ = ["catalogue", "toy"]
