"""The Day-32 multilingual emotion model: mapping, training, evaluation, reporting.

Import the submodules explicitly (``from manovia_ml.training.emotion import
taxonomy``); nothing here re-exports a symbol, because importing this package
must stay cheap and free of torch. The pipeline, in the order a run uses it:

``taxonomy`` → ``data`` → ``tiny``/``model`` → ``train`` → ``thresholds``
→ ``evaluate`` → ``report``, with ``tune`` wrapping ``train`` and
``baselines`` supplying what a fine-tune has to beat. See ADR 0032.
"""

from __future__ import annotations

__all__ = []
