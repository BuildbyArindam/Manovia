"""The Day-9 TF-IDF baseline, retrained so the comparison is apples-to-apples.

Day 9 shipped a char/word TF-IDF + calibrated logistic regression trained on the
340-row hand-written split. Comparing a transformer trained on 2,700 rows against
a linear model trained on 340 would measure the *data*, not the *model*, so this
module fits the same architecture on the same expanded training split and the
same calibration split. Both numbers then come out of one pipeline.

The architecture is deliberately the Day-9 one — char-wb 2-4 plus word 1-2
n-grams, sigmoid calibration — because the question Day 33 asks is "does the
transformer buy anything the linear model does not?", and changing the baseline
at the same time would answer a different question.

``class_weight`` is the one knob exposed: it is the scalar approximation of the
cost matrix (see ``taxonomy.class_weights_from_cost``), and ``balanced`` is the
closest sklearn can get to "penalise missing a crisis more".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from manovia_ml.training.safety.calibration import softmax
from manovia_ml.training.safety.data import SafetyExample
from manovia_ml.training.safety.taxonomy import LEVELS

__all__ = ["BaselineFit", "TfidfBaseline", "fit_tfidf_baseline"]


@dataclass(frozen=True)
class BaselineFit:
    """A fitted TF-IDF baseline plus everything needed to score with it."""

    pipeline: Any
    classes: tuple[str, ...]
    train_rows: int
    calibration_rows: int

    def probabilities(self, texts: Sequence[str]) -> list[dict[str, float]]:
        rows = self.pipeline.predict_proba(list(texts))
        return [
            {label: float(value) for label, value in zip(self.classes, row, strict=True)}
            for row in rows
        ]


def fit_tfidf_baseline(
    train: Sequence[SafetyExample],
    calibration: Sequence[SafetyExample],
    *,
    class_weight: str | None = None,
    calibration_cv: int = 3,
    max_iter: int = 5000,
) -> BaselineFit:
    """Fit the Day-9 architecture on the expanded train split.

    The calibration rows are only used to *report* held-out numbers and to
    temperature-scale — never to fit the vectoriser or the coefficients.
    """
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline
    from sklearn.preprocessing import FunctionTransformer

    from manovia_ml.training.safety.preprocess import preprocess_batch

    texts = [example.text for example in train]
    labels = [example.label for example in train]
    logistic = LogisticRegression(
        C=1.0,
        max_iter=max_iter,
        class_weight=class_weight,
        solver="lbfgs",
    )
    pipeline: Any = Pipeline(
        [
            ("prep", FunctionTransformer(preprocess_batch, validate=False)),
            (
                "features",
                FeatureUnion(
                    [
                        (
                            "chars",
                            TfidfVectorizer(
                                analyzer="char_wb",
                                ngram_range=(2, 4),
                                min_df=1,
                                sublinear_tf=True,
                                lowercase=False,
                            ),
                        ),
                        (
                            "words",
                            TfidfVectorizer(
                                analyzer="word",
                                token_pattern=r"(?u)\S+",
                                ngram_range=(1, 2),
                                min_df=1,
                                sublinear_tf=True,
                                lowercase=False,
                            ),
                        ),
                    ]
                ),
            ),
            (
                "calibrated",
                CalibratedClassifierCV(logistic, method="sigmoid", cv=calibration_cv),
            ),
        ]
    )
    pipeline.fit(texts, labels)
    classes = tuple(str(label) for label in pipeline.classes_)
    missing = [label for label in LEVELS if label not in classes]
    if missing:
        raise ValueError(f"baseline is missing levels {missing}; the corpus needs every level")
    return BaselineFit(
        pipeline=pipeline,
        classes=classes,
        train_rows=len(train),
        calibration_rows=len(calibration),
    )


class TfidfBaseline:
    """A thin wrapper so the evaluator can score a baseline like a model."""

    def __init__(self, fit: BaselineFit, *, temperature: float = 1.0) -> None:
        self._fit = fit
        self._temperature = temperature

    def probabilities(self, examples: Sequence[SafetyExample]) -> list[dict[str, float]]:
        """Calibrated probabilities, re-softmaxed at ``temperature`` for comparability."""
        raw = self._fit.probabilities([example.text for example in examples])
        out: list[dict[str, float]] = []
        for row in raw:
            logits = [_logit(row.get(label, 0.0)) for label in LEVELS]
            scaled = softmax([value / self._temperature for value in logits])
            out.append(dict(zip(LEVELS, scaled, strict=True)))
        return out


def _logit(probability: float) -> float:
    import math

    return math.log(max(probability, 1e-12) / max(1.0 - probability, 1e-12))
