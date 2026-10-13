"""Config as data: YAML in, validated object out, nothing hidden in the code.

Rules the models below enforce (all tested in ``ml/tests/test_config.py``):

* **Unknown keys are errors.** A typo like ``max_iters:`` or ``seed_a:`` must stop
  the run, not be quietly ignored while a default trains a different model.
* **No mutable defaults.** Every model is ``frozen=True``: a config that a run
  can mutate halfway through is not a record of that run.
* **Bounds are declared, not documented.** Seeds fit in 4 bytes, ``max_iter`` is
  positive, tolerances are non-negative — the schema says so, so both the CLI
  and the tests get the same answer.
* **The resolved config is logged whole.** MLflow gets the canonical JSON of the
  validated object *and* a sha256 fingerprint, so two runs can be compared
  without diffing YAML by hand.

The task brief for Day 31 asks for "YAML configs, pydantic-validated"; this is
that, plus the fingerprint that makes the validation useful at run time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from manovia_ml.hashing import sha256_text
from manovia_ml.paths import config_dir

__all__ = [
    "ConfigError",
    "DataConfig",
    "EvaluationConfig",
    "ExpectedMetrics",
    "LogRegConfig",
    "ModelConfig",
    "SgdConfig",
    "SplitSpec",
    "TfidfConfig",
    "TrackingConfig",
    "TrainConfig",
    "compare_metrics",
    "config_fingerprint",
    "expected_metrics_path",
    "load_config",
    "load_config_dict",
    "load_expected_metrics",
    "write_expected_metrics",
]

MetricName = Literal["accuracy", "macro_f1", "weighted_f1", "log_loss"]


class ConfigError(ValueError):
    """A config file that cannot be trusted to describe a run."""


class StrictModel(BaseModel):
    """Base: reject unknown keys, forbid mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SplitSpec(StrictModel):
    """One dataset file, optionally pinned to a version."""

    path: str = Field(min_length=1, description="Path, relative to the ML project root.")
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rows: int | None = Field(default=None, gt=0)

    @field_validator("sha256", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> object:
        """Accept an upper-case hash; store lower-case.

        Runs `before` so it happens ahead of the pattern check — a pin pasted
        from a `sha256sum` output is the normal way this value arrives, and
        rejecting it for case would be noise, not safety.
        """

        return value.lower() if isinstance(value, str) else value


class DataConfig(StrictModel):
    train: SplitSpec
    eval: SplitSpec
    text_field: str = "text"
    label_field: str = "label"
    lang_field: str | None = "lang"
    #: Explicit class order. Set it: sorted() on labels is stable across
    #: machines, but it is *lexicographic*, which quietly reorders a confusion
    #: matrix compared to the order the data was written in.
    labels: list[str] | None = None


class TfidfConfig(StrictModel):
    word_ngram_range: tuple[int, int] = (1, 2)
    char_ngram_range: tuple[int, int] | None = None
    min_df: int = Field(default=1, ge=1)
    max_features: int | None = Field(default=50_000, gt=0)
    sublinear_tf: bool = True
    lowercase: bool = True

    @field_validator("word_ngram_range", "char_ngram_range")
    @classmethod
    def _ordered(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is None:
            return None
        low, high = value
        if low < 1 or high < low:
            raise ValueError(f"ngram range must be 1 <= low <= high, got {value}")
        return value


class SgdConfig(StrictModel):
    """Linear model trained with a fixed number of SGD passes.

    This is the smoke-run workhorse: ``max_iter``*``n_rows`` is a controlled
    amount of work on any CPU, and the result genuinely depends on the seed
    (the sample order is shuffled), which is what makes the seed-sensitivity
    check meaningful.
    """

    kind: Literal["tfidf_sgd"] = "tfidf_sgd"
    tfidf: TfidfConfig = TfidfConfig()
    alpha: float = Field(default=1e-4, gt=0)
    max_iter: int = Field(default=30, ge=1, le=10_000)
    average: bool = False
    class_weight: Literal["balanced"] | None = None


class LogRegConfig(StrictModel):
    """L2 logistic regression.

    ``solver="lbfgs"`` is deterministic by construction — the same data gives
    the same coefficients whatever the seed is, which is a feature for a
    published number and the reason the seed-sensitivity check runs on the SGD
    kind instead (see ``ml/tests/test_reproducibility.py``).
    """

    kind: Literal["tfidf_logreg"] = "tfidf_logreg"
    tfidf: TfidfConfig = TfidfConfig()
    C: float = Field(default=1.0, gt=0)
    max_iter: int = Field(default=200, ge=1, le=10_000)
    # Only solvers that handle a multiclass problem directly. `liblinear` is
    # deliberately absent: scikit-learn 1.9 refuses it for n_classes >= 3, so
    # offering it in the schema would mean offering a config that cannot run.
    solver: Literal["lbfgs", "newton-cholesky"] = "lbfgs"
    class_weight: Literal["balanced"] | None = None


ModelConfig = Annotated[SgdConfig | LogRegConfig, Field(discriminator="kind")]


class EvaluationConfig(StrictModel):
    #: How far two runs of the same config + seed may drift. 1e-6 is "the same
    #: number, printed differently": it tolerates BLAS/thread/BLAS-version noise
    #: and catches a genuinely different model.
    tolerance: float = Field(default=1e-6, ge=0.0)
    metrics: list[MetricName] = ["accuracy", "macro_f1", "weighted_f1"]
    # `log_loss` is available but opt-in: it needs predict_proba, and it is the
    # metric that still moves when a tiny eval split makes accuracy look frozen.
    confusion_matrix: bool = True


class TrackingConfig(StrictModel):
    experiment: str = "manovia-ml"
    #: None → ``MLFLOW_TRACKING_URI`` → ``file:<ml root>/mlruns`` (see paths.py).
    tracking_uri: str | None = None
    log_environment: bool = True
    run_name: str | None = None
    tags: dict[str, str] = {}


class TrainConfig(StrictModel):
    """The whole description of one training run."""

    name: str = Field(min_length=1)
    seed: int = Field(ge=0, lt=2**32)
    notes: str | None = None
    data: DataConfig
    model: ModelConfig
    evaluation: EvaluationConfig = EvaluationConfig()
    tracking: TrackingConfig = TrackingConfig()
    #: BLAS/OpenMP thread pinning. 1 = reproducible everywhere; None = leave the
    #: pools alone (faster, and the tolerance covers the difference).
    threads: int | None = Field(default=1, ge=1)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def config_fingerprint(config: TrainConfig) -> str:
    """sha256 of the canonical JSON of a validated config.

    Logged as ``config.sha256``. Two runs whose fingerprints match were given
    the same inputs *after* defaults were applied, which YAML diffs cannot tell
    you.
    """

    return sha256_text(_canonical(config.model_dump(mode="json")))


def load_config_dict(raw: dict[str, Any], *, origin: str = "<dict>") -> TrainConfig:
    try:
        return TrainConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{origin}: invalid config\n{exc}") from exc


def load_config(path: str | Path) -> TrainConfig:
    """Load and validate a YAML config.

    Relative paths resolve against ``ml/configs/`` so ``CONFIG=toy.yaml`` works
    from anywhere; an explicit ``configs/...`` or absolute path also works.
    """

    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.is_file():
        candidate = config_dir() / path
    if not candidate.is_file():
        raise ConfigError(f"config not found: {path} (looked at {candidate})")
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{candidate}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{candidate}: config must be a mapping at the top level")
    return load_config_dict(raw, origin=str(candidate))


class ExpectedMetrics(StrictModel):
    """A committed baseline for one config: what the last verified run produced.

    Written by ``make ml-record CONFIG=...`` (i.e. ``--record-expected``) and
    checked by every later ``make ml-train``. A drift bigger than
    ``tolerance`` fails the run and prints both sides — which is the cheap,
    visible half of "is this reproducible?" that a test suite cannot answer for
    a config someone edits by hand.
    """

    config: str
    seed: int
    tolerance: float
    metrics: dict[str, float]
    run_id: str | None = None
    recorded: str | None = None
    note: str | None = None


def expected_metrics_path(config_path: str | Path) -> Path:
    """Where this config's recorded baseline lives: ``expected/`` beside it.

    Resolved the same way `load_config` resolves the config itself, so both of
    these land in the same file:

    * ``configs/toy_emotion_sgd.yaml`` → ``configs/expected/toy_emotion_sgd.json``
      (the convention every command in ml/README.md documents), and
    * a config written to a temporary directory → a baseline beside *it*, not in
      the installed package's configs directory.

    The second case is a bug fix, not a nicety: keying the baseline off the file
    *name* alone meant a run whose config lived anywhere else wrote its
    expectations into this repository — which is how a test named
    ``baseline_test.yaml`` once left ``ml/configs/expected/baseline_test.json``
    behind (see tests/test_config.py::test_a_temporary_config_keeps_its_baseline_beside_it).
    """

    candidate = Path(config_path)
    if not candidate.is_absolute() and not candidate.is_file():
        candidate = config_dir() / candidate
    if not candidate.is_absolute():
        # A relative path that exists is cwd-relative — the same file load_config
        # would open — so make that explicit before taking its parent.
        candidate = candidate.resolve()
    return candidate.parent / "expected" / f"{candidate.stem}.json"


def load_expected_metrics(config_path: str | Path) -> ExpectedMetrics | None:
    path = expected_metrics_path(config_path)
    if not path.is_file():
        return None
    return ExpectedMetrics.model_validate_json(path.read_text(encoding="utf-8"))


def write_expected_metrics(config_path: str | Path, expected: ExpectedMetrics) -> Path:
    path = expected_metrics_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = expected.model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def compare_metrics(
    observed: dict[str, float], expected: dict[str, float], *, tolerance: float
) -> list[str]:
    """Return human-readable differences; empty list means "within tolerance"."""

    problems: list[str] = []
    for name in sorted(expected):
        if name not in observed:
            problems.append(f"{name}: missing from this run (expected {expected[name]})")
            continue
        delta = abs(observed[name] - expected[name])
        if delta > tolerance:
            problems.append(
                f"{name}: {observed[name]:.6f} vs expected {expected[name]:.6f} "
                f"(delta {delta:.6f} > tolerance {tolerance:g})"
            )
    return problems
