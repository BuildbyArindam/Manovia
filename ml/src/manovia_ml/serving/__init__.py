"""Serving: load a logged run and predict with it.

This is a *reference* path, not a deployed service. Day 31 builds the platform —
tracking, configs, reproducibility, cards — and deliberately does not touch the
API surface a user sees. When a model is actually going to serve traffic, the
decision about *where* it runs belongs to the Manovia backend, behind the
interfaces it already has (``app/services/nlp`` for emotion, the rules-first
safety path for everything else) and with the ADR that goes with it.

What matters today is that a run id is enough to load the exact model later:
:func:`manovia_ml.serving.predictor.EmotionPredictor.from_run` fetches the
artefact from the run's own store, and refuses to load a bundle made with a
different scikit-learn version unless you insist — the version skew that has
bitten this project before is a load-time error here, not a silent metric drift.
"""

from __future__ import annotations

__all__ = ["artifacts", "predictor"]
