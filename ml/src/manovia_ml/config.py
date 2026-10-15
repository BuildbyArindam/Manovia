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
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from manovia_ml.hashing import sha256_text
from manovia_ml.paths import config_dir

__all__ = [
    "RETRIEVAL_ENCODER_KINDS",
    "RETRIEVAL_RERANKER_KINDS",
    "ConfigError",
    "DataConfig",
    "DistillConfig",
    "DistillStudentConfig",
    "DistillTrainingConfig",
    "EmotionLoraConfig",
    "EmotionReportConfig",
    "EmotionRunConfig",
    "EvaluationConfig",
    "ExpectedMetrics",
    "LogRegConfig",
    "LoraAdapterConfig",
    "LoraTrainingConfig",
    "ModelConfig",
    "OptunaConfig",
    "RetrievalCalibrationConfig",
    "RetrievalConfig",
    "RetrievalDataConfig",
    "RetrievalEncoderConfig",
    "RetrievalEvalConfig",
    "RetrievalFeaturesConfig",
    "RetrievalHybridConfig",
    "RetrievalRerankerConfig",
    "RetrievalTrainingConfig",
    "SafetyCalibrationConfig",
    "SafetyConformalConfig",
    "SafetyCostConfig",
    "SafetyLoraConfig",
    "SafetyReportConfig",
    "SafetyRunConfig",
    "SafetyTfidfConfig",
    "SgdConfig",
    "SplitSpec",
    "TfidfConfig",
    "ThresholdConfig",
    "TrackingConfig",
    "TrainConfig",
    "compare_metrics",
    "config_fingerprint",
    "expected_metrics_path",
    "load_config",
    "load_config_dict",
    "load_distill_config",
    "load_expected_metrics",
    "load_retrieval_config",
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
    #: Day 32: a real training run has three splits, not two. ``eval`` is the
    #: model-selection split (early stopping); ``validation`` is where per-label
    #: thresholds are tuned; ``test`` is the held-out set the number is quoted
    #: from. The linear configs use ``eval`` for both, which is why they leave
    #: these two unset — and that is stated rather than implied.
    validation: SplitSpec | None = None
    test: SplitSpec | None = None
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


class LoraAdapterConfig(StrictModel):
    """LoRA adapter shape (Day 32). Every number here is swept or chosen, never defaulted blindly.

    ``target_modules`` defaults to the query and value projections: that is what
    the LoRA paper attaches to for encoder models, and it keeps the adapter small
    relative to the classification head. ``bias="none"`` and
    ``freeze_base=False`` are the PEFT defaults for a reason — the base weights
    are already frozen by LoRA itself, and the head must stay trainable.
    """

    rank: int = Field(default=8, ge=1, le=256)
    alpha: int = Field(default=16, ge=1, le=512)
    dropout: float = Field(default=0.1, ge=0.0, lt=1.0)
    target_modules: tuple[str, ...] = ("query", "value")
    bias: Literal["none", "all", "lora_only"] = "none"
    freeze_base: bool = False


class LoraTrainingConfig(StrictModel):
    """The optimisation loop's settings, all of them config-resident."""

    epochs: int = Field(default=3, ge=1, le=100)
    batch_size: int = Field(default=16, ge=1, le=1024)
    eval_batch_size: int = Field(default=32, ge=1, le=2048)
    learning_rate: float = Field(default=3e-4, gt=0.0, lt=1.0)
    weight_decay: float = Field(default=0.01, ge=0.0, lt=1.0)
    warmup_ratio: float = Field(default=0.06, ge=0.0, lt=1.0)
    #: Gradient clipping. Required, not optional: a 3e-4 LoRA run on a small
    #: batch spikes without it, and "it trained on my machine" is not a result.
    max_grad_norm: float = Field(default=1.0, gt=0.0)
    #: Label smoothing for a sigmoid loss: 0.05 means a positive target becomes
    #: 0.95 and a negative one 0.05. Soft targets already in the data (the
    #: mapping's weights) are smoothed toward this same floor.
    label_smoothing: float = Field(default=0.02, ge=0.0, lt=0.5)
    class_weighting: Literal["balanced", "none"] = "balanced"
    scheduler: Literal["cosine", "linear", "constant"] = "cosine"
    mixed_precision: Literal["auto", "fp16", "bf16", "off"] = "auto"
    gradient_accumulation_steps: int = Field(default=1, ge=1, le=64)
    #: Early stopping on the validation split. ``patience=0`` disables it, which
    #: a smoke run wants (there are not enough steps to have a trend).
    early_stopping_patience: int = Field(default=2, ge=0, le=20)
    early_stopping_min_delta: float = Field(default=0.001, ge=0.0)
    #: Hard cap on optimiser steps. The smoke run uses it to mean "a few steps on
    #: toy data"; a real run leaves it null so ``epochs`` is the whole story.
    max_steps: int | None = Field(default=None, gt=0)
    log_every_steps: int = Field(default=25, ge=1)


class ThresholdConfig(StrictModel):
    """Per-label decision thresholds, tuned on the validation split.

    Tuned on validation, **never** on the test set: a threshold fitted on the
    rows you then report measures the fitting, not the model.
    """

    enabled: bool = True
    search_min: float = Field(default=0.05, gt=0.0, lt=1.0)
    search_max: float = Field(default=0.95, gt=0.0, lt=1.0)
    search_steps: int = Field(default=19, ge=3, le=201)
    metric: Literal["f1", "balanced_accuracy"] = "f1"
    #: A label with fewer than this many positives in the validation split keeps
    #: the default threshold: one positive cannot support a fitted threshold.
    min_support: int = Field(default=3, ge=1)
    default: float = Field(default=0.5, gt=0.0, lt=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> ThresholdConfig:
        if self.search_min >= self.search_max:
            raise ValueError("thresholds.search_min must be below search_max")
        return self


class OptunaConfig(StrictModel):
    """The small sweep the brief allows: LoRA rank x learning rate, <= 12 trials."""

    enabled: bool = False
    n_trials: int = Field(default=8, ge=1, le=12)
    rank_choices: tuple[int, ...] = (4, 8, 16, 32)
    learning_rate_low: float = Field(default=1e-4, gt=0.0, lt=1.0)
    learning_rate_high: float = Field(default=3e-3, gt=0.0, lt=1.0)
    #: Which split the sweep scores. Always the validation split.
    metric: Literal["macro_f1", "loss"] = "macro_f1"
    seed: int | None = Field(default=None, ge=0, lt=2**32)
    timeout_seconds: int | None = Field(default=None, gt=0)

    @field_validator("rank_choices")
    @classmethod
    def _ranks_are_sane(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("optuna.rank_choices must not be empty")
        if any(rank < 1 or rank > 256 for rank in value):
            raise ValueError(f"optuna.rank_choices must be within [1, 256], got {value}")
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def _rates_are_ordered(self) -> OptunaConfig:
        if self.learning_rate_low >= self.learning_rate_high:
            raise ValueError("optuna.learning_rate_low must be below learning_rate_high")
        return self


class EmotionReportConfig(StrictModel):
    """What the evaluation writes: intervals, calibration bins, error analysis."""

    bootstrap_resamples: int = Field(default=2000, ge=100, le=50000)
    confidence_level: float = Field(default=0.95, gt=0.0, lt=1.0)
    calibration_bins: int = Field(default=10, ge=2, le=50)
    error_cases: int = Field(default=20, ge=1, le=200)
    #: Baselines to compare against, in report order. ``keyword`` is always
    #: computed offline; ``baseline`` is the Day-6 off-the-shelf checkpoint and
    #: needs the `nlp` extra plus a network download.
    baselines: tuple[Literal["keyword", "sentiment", "baseline"], ...] = ("keyword", "baseline")


class EmotionRunConfig(StrictModel):
    """Everything emotion-specific that is not the model or the data."""

    thresholds: ThresholdConfig = ThresholdConfig()
    optuna: OptunaConfig = OptunaConfig()
    report: EmotionReportConfig = EmotionReportConfig()
    #: Where a packaged artefact is written, relative to the ML root.
    artifact_dir: str = "artifacts/emotion"
    #: Name of the packaged model directory (a version, not a date).
    artifact_name: str = "manovia-emotion-v1"


class EmotionLoraConfig(StrictModel):
    """A LoRA fine-tune of a multilingual encoder into Manovia's taxonomy."""

    kind: Literal["emotion_lora"] = "emotion_lora"
    #: Hub id, or a local directory, or ``tiny://xlm-roberta`` for the offline
    #: stand-in (``training/emotion/tiny.py``). Never a hard-coded path.
    base_model: str = Field(min_length=1)
    tokenizer_max_length: int = Field(default=128, ge=8, le=512)
    lora: LoraAdapterConfig = LoraAdapterConfig()
    training: LoraTrainingConfig = LoraTrainingConfig()


class SafetyCostConfig(StrictModel):
    """The cost matrix the loss and the decision rule use (Day 33).

    The matrix itself lives in ``training/safety/taxonomy.py`` because it is a
    product decision, not a hyperparameter; what is config-resident is *whether*
    it is used, how it is normalised, and how much label smoothing is applied to
    the targets before the cost lookup.
    """

    #: Which matrix. One exists today; naming it means adding a second is an act.
    matrix: Literal["default"] = "default"
    #: Row-normalise the loss (see ``training/safety/cost.NORMALISE_PER_ROW``).
    normalise_per_row: bool = True
    #: Smoothing applied to the one-hot target before the cost weights are
    #: looked up. 0.0 means "the truth is the truth".
    label_smoothing: float = Field(default=0.0, ge=0.0, lt=0.5)
    #: Whether the inference-time decision rule is the expected-cost argmin
    #: (true, the point of the matrix) or a plain argmax.
    cost_decision_rule: bool = True


class SafetyCalibrationConfig(StrictModel):
    """Temperature scaling: one parameter, fitted on the calibration split."""

    enabled: bool = True
    #: Reliability-diagram bins. Also the ECE bin count.
    bins: int = Field(default=10, ge=2, le=50)
    #: Search bounds for the temperature. Wide on purpose: a badly over-confident
    #: small model needs a T well above 2.
    lower: float = Field(default=0.05, gt=0.0, lt=10.0)
    upper: float = Field(default=10.0, gt=0.05, le=100.0)


class SafetyConformalConfig(StrictModel):
    """Split conformal prediction sets and the abstention policy."""

    enabled: bool = True
    #: Target miscoverage. 0.05 -> 95% coverage.
    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    #: The only policy implemented: a set containing a level >= MEDIUM makes the
    #: message MEDIUM or above. See ``training/safety/conformal.abstain_level``.
    policy: Literal["abstain_medium"] = "abstain_medium"


class SafetyReportConfig(StrictModel):
    """What the evaluation writes: intervals, per-language tables, error cases."""

    bootstrap_resamples: int = Field(default=2000, ge=100, le=50000)
    confidence_level: float = Field(default=0.95, gt=0.0, lt=1.0)
    error_cases: int = Field(default=20, ge=1, le=200)
    #: Whether to fit the TF-IDF baseline on the same data and report both.
    compare_baseline: bool = True


class SafetyRunConfig(StrictModel):
    """Everything safety-specific that is not the model or the data (Day 33)."""

    #: The baseline's class weighting, hoisted here so both model kinds agree.
    baseline_class_weight: Literal["balanced", "none"] = "balanced"
    cost: SafetyCostConfig = SafetyCostConfig()
    calibration: SafetyCalibrationConfig = SafetyCalibrationConfig()
    conformal: SafetyConformalConfig = SafetyConformalConfig()
    report: SafetyReportConfig = SafetyReportConfig()
    #: Where a packaged artefact is written, relative to the ML root.
    artifact_dir: str = "artifacts/safety"
    #: Name of the packaged model directory (a version, not a date).
    artifact_name: str = "manovia-safety-v1"


class SafetyLoraConfig(StrictModel):
    """A cost-sensitive LoRA fine-tune of a multilingual encoder into five levels."""

    kind: Literal["safety_lora"] = "safety_lora"
    #: Hub id, a local directory, or ``tiny://xlm-roberta`` for the offline
    #: stand-in. Never a hard-coded path.
    base_model: str = Field(min_length=1)
    tokenizer_max_length: int = Field(default=96, ge=8, le=512)
    lora: LoraAdapterConfig = LoraAdapterConfig()
    training: LoraTrainingConfig = LoraTrainingConfig()


class SafetyTfidfConfig(StrictModel):
    """The Day-9 TF-IDF baseline, fit on the same expanded corpus.

    Exists so the transformer's numbers can be compared against something trained
    on the *same rows*: a linear model trained on 340 hand-written cases is a
    different experiment, not a baseline.
    """

    kind: Literal["safety_tfidf"] = "safety_tfidf"
    #: ``balanced`` is the scalar approximation of the cost matrix; ``None`` is
    #: the unweighted Day-9 operating point.
    class_weight: Literal["balanced", "none"] = "balanced"
    calibration_cv: int = Field(default=3, ge=2, le=10)
    max_iter: int = Field(default=5000, ge=100, le=100_000)


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


ModelConfig = Annotated[
    SgdConfig | LogRegConfig | EmotionLoraConfig | SafetyLoraConfig | SafetyTfidfConfig,
    Field(discriminator="kind"),
]


class TrainConfig(StrictModel):
    """The whole description of one training run."""

    name: str = Field(min_length=1)
    seed: int = Field(ge=0, lt=2**32)
    notes: str | None = None
    data: DataConfig
    model: ModelConfig
    evaluation: EvaluationConfig = EvaluationConfig()
    tracking: TrackingConfig = TrackingConfig()
    #: Day 32: thresholds, the Optuna sweep and the report's shape. Only a
    #: `emotion_lora` run has any use for it, and the validator below enforces
    #: exactly that pairing — a config that tunes thresholds for a TF-IDF model
    #: is a mistake, not a feature.
    emotion: EmotionRunConfig | None = None
    #: Day 33: the cost matrix, calibration, conformal and report settings. The
    #: same pairing rule as `emotion:` — see the validator below.
    safety: SafetyRunConfig | None = None
    #: BLAS/OpenMP thread pinning. 1 = reproducible everywhere; None = leave the
    #: pools alone (faster, and the tolerance covers the difference).
    threads: int | None = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _emotion_pairing(self) -> TrainConfig:
        needs_emotion = isinstance(self.model, EmotionLoraConfig)
        if needs_emotion and self.emotion is None:
            raise ValueError("model.kind 'emotion_lora' requires the `emotion:` section")
        if not needs_emotion and self.emotion is not None:
            raise ValueError("the `emotion:` section only applies to model.kind 'emotion_lora'")
        if needs_emotion and self.data.validation is None:
            raise ValueError(
                "model.kind 'emotion_lora' requires data.validation: thresholds are tuned on "
                "the validation split, never on the test split"
            )
        return self

    @model_validator(mode="after")
    def _safety_pairing(self) -> TrainConfig:
        """A safety run needs its section, and only a safety run may have one.

        The same rule as `_emotion_pairing`, plus the split mapping, because that
        is the part a config author knows and everyone else does not:
        ``data.eval`` is the *dev* split (model selection), ``data.validation``
        is the *calibration* split (temperature and conformal quantile), and
        ``data.test`` is the frozen Day-9 file. A safety run without a
        calibration split would silently temperature-scale on dev, which is the
        bug this check exists to prevent.
        """
        needs_safety = isinstance(self.model, (SafetyLoraConfig, SafetyTfidfConfig))
        if needs_safety and self.safety is None:
            raise ValueError(
                "model.kind 'safety_lora'/'safety_tfidf' requires the `safety:` section "
                "(cost matrix, calibration, conformal)"
            )
        if not needs_safety and self.safety is not None:
            raise ValueError(
                "the `safety:` section only applies to model.kind 'safety_lora'/'safety_tfidf'"
            )
        if needs_safety and self.data.validation is None:
            raise ValueError(
                "a safety run requires data.validation: it is the *calibration* split, where "
                "the temperature and the conformal quantile are fitted"
            )
        if needs_safety and self.data.test is None:
            raise ValueError(
                "a safety run requires data.test: the frozen Day-9 split is the only place a "
                "number may be quoted from"
            )
        return self


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def config_fingerprint(config: BaseModel) -> str:
    """sha256 of the canonical JSON of a validated config.

    Logged as ``config.sha256``. Two runs whose fingerprints match were given
    the same inputs *after* defaults were applied, which YAML diffs cannot tell
    you. Any validated config object is acceptable (TrainConfig, DistillConfig,
    RetrievalConfig): the fingerprint is of the canonical dump, not of a shape.
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


class DistillStudentConfig(StrictModel):
    """The student a distillation run trains (Day 34).

    ``kind: linear_hash`` is the offline smoke student: a multinomial logistic
    regression over hashed n-gram features, trained with the exact
    temperature-scaled distillation loss in numpy. It exists so the whole
    Day-34 path (distil → ONNX → INT8 → gates → registry) runs on a CPU with
    no torch and no download. ``kind: transformer`` names the real student
    (a MiniLM/DistilBERT-class multilingual encoder) and is only runnable
    where torch+transformers are installed (Colab/Kaggle); the trainer
    refuses with instructions anywhere else rather than half-running.
    """

    kind: Literal["linear_hash", "transformer"] = "linear_hash"
    #: Hub id for ``transformer`` (e.g. microsoft/multilingual-MiniLM-L12-H384).
    #: Empty for the linear student. Never hard-coded in code (AGENTS.md).
    base_model: str = ""
    #: Hashed-feature width for the linear student. 1024 keeps the fp32 ONNX
    #: at ~20 KB for five classes while covering the toy vocabularies.
    feature_dim: int = Field(default=1024, ge=64, le=65536)
    word_ngram_max: int = Field(default=2, ge=1, le=3)
    char_ngram_min: int = Field(default=3, ge=2, le=6)
    char_ngram_max: int = Field(default=5, ge=2, le=8)
    max_length_chars: int = Field(default=2000, ge=64, le=20000)

    @model_validator(mode="after")
    def _char_range(self) -> DistillStudentConfig:
        if self.char_ngram_max < self.char_ngram_min:
            raise ValueError("student.char_ngram_max must be >= char_ngram_min")
        if self.kind == "transformer" and not self.base_model.strip():
            raise ValueError("student.kind 'transformer' requires student.base_model")
        return self


class DistillTrainingConfig(StrictModel):
    """The distillation loss and optimisation loop, all config-resident."""

    #: Softmax temperature for teacher/student distributions.
    temperature: float = Field(default=2.0, gt=0.0, le=20.0)
    #: Weight on the distillation (soft) term; 1-alpha weights hard-label CE.
    alpha: float = Field(default=0.7, ge=0.0, le=1.0)
    epochs: int = Field(default=60, ge=1, le=2000)
    learning_rate: float = Field(default=0.5, gt=0.0, lt=10.0)
    l2: float = Field(default=1e-4, ge=0.0, lt=1.0)
    batch_size: int = Field(default=32, ge=1, le=8192)


class DistillConfig(StrictModel):
    """One distillation run: a Day-32/33 teacher into a small student."""

    name: str = Field(min_length=1)
    task: Literal["safety", "emotion"]
    seed: int = Field(ge=0, lt=2**32)
    notes: str | None = None
    #: The teacher this student distils. ``sklearn_tfidf`` trains a TF-IDF +
    #: logistic-regression stand-in on the same rows (the sandbox has no
    #: Day-32/33 weights); ``trained_artifact`` names a packaged Day-32/33
    #: artefact directory for the real run. The name is recorded in every
    #: report so a smoke number can never be quoted as a teacher number.
    teacher_kind: Literal["sklearn_tfidf", "trained_artifact"] = "sklearn_tfidf"
    teacher_artifact_dir: str = ""
    #: Declared teacher size (MB) for the size-ratio gate and the tradeoff
    #: table. Measured for the smoke stand-in at run time when 0.
    teacher_size_mb: float = Field(default=0.0, ge=0.0)
    data: DataConfig
    student: DistillStudentConfig = DistillStudentConfig()
    training: DistillTrainingConfig = DistillTrainingConfig()
    tracking: TrackingConfig = TrackingConfig()
    threads: int | None = Field(default=1, ge=1)


def load_distill_config(path: str | Path) -> DistillConfig:
    """Load and validate a distillation YAML (``extra=forbid``, like TrainConfig)."""

    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.is_file():
        candidate = config_dir() / path
    if not candidate.is_file():
        raise ConfigError(f"distill config not found: {path} (looked at {candidate})")
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{candidate}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{candidate}: config must be a mapping at the top level")
    try:
        return DistillConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{candidate}: invalid distill config\n{exc}") from exc


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


# --------------------------------------------------------------------- #
# Day 35 — fine-tuned retrieval                                            #
# --------------------------------------------------------------------- #
#
# The same rules as every other config here: unknown keys are errors, models are
# frozen, bounds are declared rather than documented, and the resolved config is
# logged whole. What is retrieval-specific is the *pairing* of the sections: a
# `sentence_transformers` encoder needs a model id, a `lexical` reranker needs
# coefficients, and thresholds may only ever be fitted on the calibration split
# (the validator says so, because a threshold fitted on test is not a threshold).

RETRIEVAL_ENCODER_KINDS: tuple[str, ...] = (
    "projection",
    "tfidf",
    "hashing",
    "sentence_transformers",
)
RETRIEVAL_RERANKER_KINDS: tuple[str, ...] = ("none", "lexical", "transformer")


class RetrievalDataConfig(StrictModel):
    """Where the eval set and its splits live."""

    #: The expanded eval set (hand-written + synthetic). DVC output.
    eval_set: str = "data/retrieval/expanded.jsonl"
    #: Directory holding train/dev/calibration/test JSONL + manifest.json.
    splits_dir: str = "data/retrieval_splits"
    #: The knowledge corpus directory. Empty = resolve from the repository root.
    corpus_dir: str = ""
    chunk_words: int = Field(default=220, ge=20, le=600)
    chunk_overlap_words: int = Field(default=50, ge=0)
    #: Seed used to cut the splits (must match the DVC stage that wrote them).
    split_seed: int = Field(default=20261013, ge=0, lt=2**32)

    @model_validator(mode="after")
    def _overlap(self) -> RetrievalDataConfig:
        if self.chunk_overlap_words >= self.chunk_words:
            raise ValueError("chunk_overlap_words must be smaller than chunk_words")
        return self


class RetrievalFeaturesConfig(StrictModel):
    """The lexical feature space the offline projection encoder works in."""

    word_ngram_range: tuple[int, int] = (1, 2)
    char_ngram_range: tuple[int, int] = (3, 5)
    word_max_features: int = Field(default=8000, gt=0)
    char_max_features: int = Field(default=8000, gt=0)
    min_df: int = Field(default=1, ge=1)
    sublinear_tf: bool = True

    @field_validator("word_ngram_range", "char_ngram_range")
    @classmethod
    def _ordered(cls, value: tuple[int, int]) -> tuple[int, int]:
        if value[0] > value[1]:
            raise ValueError("n-gram ranges must be (low, high)")
        if value[0] < 1:
            raise ValueError("n-gram ranges start at 1")
        return value


class RetrievalEncoderConfig(StrictModel):
    """Which dense encoder a run embeds with, and how it was made."""

    kind: Literal["projection", "tfidf", "hashing", "sentence_transformers"] = "projection"
    #: Only for `sentence_transformers`: the off-the-shelf checkpoint, or a local
    #: artefact directory holding a fine-tuned one. Never hard-coded in code
    #: (AGENTS.md: never hard-code model names).
    model_id: str = ""
    #: Only for `projection`: the trained artefact directory. Empty = train one.
    artifact_dir: str = ""
    #: Embedding size for `projection` and `hashing`.
    dim: int = Field(default=128, ge=16, le=1024)
    features: RetrievalFeaturesConfig = RetrievalFeaturesConfig()

    @model_validator(mode="after")
    def _needs_model_id(self) -> RetrievalEncoderConfig:
        if self.kind == "sentence_transformers" and not self.model_id.strip():
            raise ValueError("encoder.kind 'sentence_transformers' requires encoder.model_id")
        if self.kind != "sentence_transformers" and self.model_id.strip():
            raise ValueError("encoder.model_id only applies to kind 'sentence_transformers'")
        if self.kind != "projection" and self.artifact_dir.strip():
            # Same reason as model_id: a key that only means something for one
            # kind must not be silently ignored for the others. `artifact_dir`
            # holds trained projection weights, so pointing a tfidf run at one
            # is a mistake worth refusing rather than quietly overriding.
            raise ValueError("encoder.artifact_dir only applies to kind 'projection'")
        return self


class RetrievalTrainingConfig(StrictModel):
    """Contrastive fine-tuning hyperparameters."""

    #: The loss the brief names. Implemented in `retrieval.contrastive` for the
    #: offline path and by `sentence_transformers` for the checkpoint path.
    loss: Literal["multiple_negatives_ranking"] = "multiple_negatives_ranking"
    #: Cosine-similarity temperature scale (sentence-transformers' default 20).
    scale: float = Field(default=20.0, gt=0.0)
    epochs: int = Field(default=3, ge=1, le=200)
    batch_size: int = Field(default=32, ge=2, le=512)
    lr: float = Field(default=0.05, gt=0.0)
    #: Hard negatives mined per (query, positive) pair, and how many candidates
    #: the miner looks at to find them.
    hard_negatives: int = Field(default=4, ge=0, le=32)
    mine_candidates: int = Field(default=20, ge=2, le=200)
    #: Positives sampled per query per epoch (a query with three relevant
    #: documents yields three pairs; more than one passage per document is
    #: allowed when the document has several chunks).
    max_positives_per_query: int = Field(default=2, ge=1, le=10)
    #: Model selection: which dev metric picks the epoch. Never a test metric.
    select_on: Literal["dev_mrr", "dev_recall_at_3", "last_epoch"] = "dev_mrr"
    #: Smoke runs train on a handful of rows and must not be quotable as a
    #: result; the flag travels into the MLflow tags and the report title.
    phase: Literal["train", "smoke"] = "train"


class RetrievalHybridConfig(StrictModel):
    """BM25 + dense, fused with reciprocal rank fusion."""

    bm25_k1: float = Field(default=1.2, gt=0.0, le=5.0)
    bm25_b: float = Field(default=0.75, ge=0.0, le=1.0)
    #: Title tokens count this many times in the BM25 document (the corpus's
    #: curated topical words live in the title).
    title_boost: float = Field(default=2.0, ge=1.0, le=10.0)
    #: RRF's k. 60 is the value from the original paper and the usual default;
    #: it is a config knob because "usual" is not evidence.
    fusion_k: int = Field(default=60, ge=1, le=1000)
    #: Grid searched on dev for the dense weight; BM25 gets (1 - w).
    dense_weight_grid: tuple[float, ...] = tuple(round(x * 0.1, 2) for x in range(11))
    #: Candidates the fused list hands to the reranker.
    rerank_candidates: int = Field(default=20, ge=1, le=100)
    #: Documents returned to the caller (the chat surface shows at most 3).
    top_k: int = Field(default=5, ge=1, le=20)
    #: Whether the language metadata filter and its one-shot English fallback
    #: stay on. The eval reports both postures; production keeps this true.
    language_filter: bool = False

    @field_validator("dense_weight_grid")
    @classmethod
    def _grid(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if not value:
            raise ValueError("dense_weight_grid must not be empty")
        if any(not 0.0 <= weight <= 1.0 for weight in value):
            raise ValueError("dense weights live in [0, 1]")
        return tuple(sorted(set(value)))


class RetrievalRerankerConfig(StrictModel):
    """The cross-encoder stage over the fused candidates."""

    kind: Literal["none", "lexical", "transformer"] = "lexical"
    #: Only for `transformer`: a cross-encoder checkpoint id or a local
    #: fine-tuned/distilled artefact directory.
    model_id: str = ""
    #: Trained coefficients for `lexical`. Empty = the committed default weights
    #: (a heuristic scorer, documented as untrained).
    artifact_dir: str = ""
    candidates: int = Field(default=20, ge=1, le=100)
    #: Logistic-regression strength for the distilled lexical reranker.
    c: float = Field(default=1.0, gt=0.0)
    #: Fit on train pairs only; the calibration split picks the threshold.
    train_split: Literal["train", "dev"] = "train"

    @model_validator(mode="after")
    def _needs_model_id(self) -> RetrievalRerankerConfig:
        if self.kind == "transformer" and not self.model_id.strip():
            raise ValueError("reranker.kind 'transformer' requires reranker.model_id")
        return self


class RetrievalCalibrationConfig(StrictModel):
    """ "No good answer": the abstain decision and how its threshold is chosen."""

    #: `answer_precision` takes the lowest threshold that keeps the cards it
    #: does show relevant at or above `min_answer_precision`; `f1` maximises the
    #: abstain decision's F1 instead.
    objective: Literal["answer_precision", "f1", "abstain_recall"] = "answer_precision"
    min_answer_precision: float = Field(default=0.9, ge=0.0, le=1.0)
    #: A turn counts as "answered well" when a relevant document is in the top k.
    top_k_for_answer: int = Field(default=3, ge=1, le=10)
    #: Which split the thresholds are fitted on. Test is not an option: a
    #: threshold chosen on the split it is reported on is not a measurement.
    fit_split: Literal["calibration", "dev"] = "calibration"


class RetrievalEvalConfig(StrictModel):
    """How the evaluation itself runs."""

    ks: tuple[int, ...] = (1, 3, 5)
    bootstrap: int = Field(default=2000, ge=0, le=20000)
    #: Repeats per query when timing. p95 over 52 queries x 3 repeats is a
    #: within-process measurement on one CPU, and the report says so.
    latency_repeats: int = Field(default=3, ge=1, le=20)
    #: Failure cases the report prints for the best setting.
    failure_cases: int = Field(default=10, ge=0, le=50)
    #: Split the headline numbers come from.
    split: Literal["test", "dev", "calibration", "train"] = "test"

    @field_validator("ks")
    @classmethod
    def _ks(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or any(k < 1 for k in value):
            raise ValueError("ks must be positive")
        return tuple(sorted(set(value)))


class RetrievalConfig(StrictModel):
    """One retrieval run: what to train, what to fuse, what to report."""

    name: str = Field(min_length=1)
    seed: int = Field(ge=0, lt=2**32)
    notes: str | None = None
    data: RetrievalDataConfig = RetrievalDataConfig()
    encoder: RetrievalEncoderConfig = RetrievalEncoderConfig()
    training: RetrievalTrainingConfig = RetrievalTrainingConfig()
    hybrid: RetrievalHybridConfig = RetrievalHybridConfig()
    reranker: RetrievalRerankerConfig = RetrievalRerankerConfig()
    calibration: RetrievalCalibrationConfig = RetrievalCalibrationConfig()
    evaluation: RetrievalEvalConfig = RetrievalEvalConfig()
    tracking: TrackingConfig = TrackingConfig()
    threads: int | None = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _consistency(self) -> RetrievalConfig:
        if self.reranker.candidates > self.hybrid.rerank_candidates:
            raise ValueError(
                "reranker.candidates cannot exceed hybrid.rerank_candidates: the reranker "
                "only ever sees what the fused list produced"
            )
        if self.calibration.top_k_for_answer > self.evaluation.ks[-1]:
            raise ValueError(
                "calibration.top_k_for_answer must be within the reported k range "
                f"(max {self.evaluation.ks[-1]})"
            )
        return self


def load_retrieval_config(path: str | Path) -> RetrievalConfig:
    """Load and validate a retrieval YAML (``extra=forbid``, like TrainConfig)."""

    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.is_file():
        candidate = config_dir() / path
    if not candidate.is_file():
        raise ConfigError(f"retrieval config not found: {path} (looked at {candidate})")
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{candidate}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{candidate}: config must be a mapping at the top level")
    try:
        return RetrievalConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{candidate}: invalid retrieval config\n{exc}") from exc
