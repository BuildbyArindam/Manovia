"""Day 35 retrieval calibration: the fusion weight and the abstain floors.

Written by ``ml/src/manovia_ml/retrieval/tuning.py`` (``make ml-retrieval-tune``
and ``make ml-retrieval-calibrate``) and read by
:func:`app.services.rag.factory.load_tuning`. Committed because it is calibration
data a reviewer must be able to read — a fusion weight, two abstain thresholds and
the fingerprints of the splits they were fitted on — not a model: the fitted
cross-encoder coefficients stay in ``ml/data/retrieval_artifacts/`` and the
factory falls back to the documented untrained weights without them.
"""
