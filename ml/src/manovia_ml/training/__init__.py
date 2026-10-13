"""Training: fit the model a config describes and log the run.

One entry point, `manovia_ml.training.train`, driven entirely by YAML
(`ml/configs/`): the data pins, the features, the estimator and its
hyperparameters, the metrics, the tolerance and the seed all come from the
config, so a run can be repeated from its own record without reading this
package. `python -m manovia_ml.training.train --config ...` is what `make
ml-train` and the CI `ml-smoke` job call.
"""

from __future__ import annotations

__all__ = ["train"]
