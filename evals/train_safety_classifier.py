"""Train the Day 9 baseline safety classifier from the train split ONLY.

This script is the proof of the split discipline:

* it reads ``evals/datasets/crisis_cases_train.jsonl`` to fit the model;
* it reads ``evals/datasets/crisis_cases_dev.jsonl`` only to *report* held-out
  numbers in the artifact metadata;
* it never opens the test file. A grep for the file name is asserted by
  ``backend/tests/safety/test_eval_dataset.py`` (the test split is frozen and
  must stay untouched by anything that influences the model or its thresholds).

The artifact lands in ``backend/app/ml_artifacts/crisis_v1/``:

* ``model.joblib`` — the fitted pipeline (preprocessor + TF-IDF features +
  calibrated logistic regression) plus the class list;
* ``metadata.json`` — provenance: dataset sha256, class counts, dev metrics,
  hyperparameters, date. A missing artifact degrades the app to rules-only, so
  committing the artifact is what makes the ML path available offline.

Run from the repository root:

    .venv/bin/python evals/train_safety_classifier.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sklearn.calibration import CalibratedClassifierCV  # noqa: E402
from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import accuracy_score, f1_score  # noqa: E402
from sklearn.pipeline import FeatureUnion, Pipeline  # noqa: E402
from sklearn.preprocessing import FunctionTransformer  # noqa: E402

from app.services.safety.ml_classifier import (  # noqa: E402
    LEVEL_LABELS,
    METADATA_FILENAME,
    MODEL_FILENAME,
    preprocess_batch,
)

DATASET_DIR = REPO_ROOT / "evals" / "datasets"
ARTIFACT_DIR = REPO_ROOT / "backend" / "app" / "ml_artifacts" / "crisis_v1"

#: Model hyperparameters. Picked from a manual sweep scored on the dev split
#: (min_df 1-2, C 1-8, char ranges (2,4)/(2,5)/(3,5), class weights, cv 3/5);
#: the test split played no part. Winner: min_df=1 (rare phrasings are the
#: signal on a small set), C=1.0, char_wb 2-4, no class weighting (balanced
#: weights inflated crisis mass on benign text), sigmoid calibration cv=3.
HYPERPARAMETERS: dict[str, object] = {
    "char_ngram_range": [2, 4],
    "word_ngram_range": [1, 2],
    "min_df": 1,
    "logistic_C": 1.0,
    "logistic_max_iter": 5000,
    "class_weight": None,
    "calibration_method": "sigmoid",
    "calibration_cv": 3,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_split(name: str) -> tuple[list[str], list[str]]:
    """Texts and labels for one split. Labels are lower-cased wire forms."""
    path = DATASET_DIR / f"crisis_cases_{name}.jsonl"
    texts: list[str] = []
    labels: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            texts.append(case["text"])
            labels.append(case["label"].lower())
    return texts, labels


def build_pipeline() -> Pipeline:
    """The baseline: char-wb + word TF-IDF, then a calibrated logistic model.

    Char n-grams are the workhorse here: they survive romanised Indic typos,
    leet substitutions and fused words without the language-specific stemming
    a word model would need. Balanced class weights push recall on the rare
    crisis classes, which is the metric this system is graded on.
    """
    class_weight = HYPERPARAMETERS["class_weight"]
    logistic = LogisticRegression(
        C=float(HYPERPARAMETERS["logistic_C"]),
        max_iter=int(HYPERPARAMETERS["logistic_max_iter"]),
        class_weight=class_weight,  # type: ignore[arg-type]
        solver="lbfgs",
    )
    return Pipeline(
        [
            # Same normalisation the serving path applies
            # (app.services.safety.ml_classifier.preprocess_batch), shipped
            # inside the artifact so training and inference can never drift.
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
                                min_df=int(HYPERPARAMETERS["min_df"]),
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
                                min_df=int(HYPERPARAMETERS["min_df"]),
                                sublinear_tf=True,
                                lowercase=False,
                            ),
                        ),
                    ]
                ),
            ),
            (
                "calibrated",
                CalibratedClassifierCV(
                    logistic,
                    method=str(HYPERPARAMETERS["calibration_method"]),
                    cv=int(HYPERPARAMETERS["calibration_cv"]),
                ),
            ),
        ]
    )


def main() -> None:
    train_texts, train_labels = load_split("train")
    dev_texts, dev_labels = load_split("dev")

    missing = [label for label in LEVEL_LABELS if label not in set(train_labels)]
    if missing:
        raise SystemExit(f"train split is missing labels: {missing}")

    pipeline = build_pipeline()
    pipeline.fit(train_texts, train_labels)

    # sklearn orders classes_ its own way; the artifact stores that order and
    # the serving path zips probabilities against it, so only the SET must
    # match the level vocabulary.
    classes = tuple(str(item) for item in pipeline.named_steps["calibrated"].classes_)
    if set(classes) != set(LEVEL_LABELS):
        raise SystemExit(f"fitted classes {classes} do not cover {LEVEL_LABELS}")

    # Held-out report on dev only. This number goes in the metadata so the
    # artifact carries its own honest report card.
    dev_predictions = list(pipeline.predict(dev_texts))
    crisis_true = [label in ("high", "imminent") for label in dev_labels]
    crisis_pred = [label in ("high", "imminent") for label in dev_predictions]
    crisis_recall = (
        sum(a and b for a, b in zip(crisis_true, crisis_pred)) / sum(crisis_true)
        if any(crisis_true)
        else None
    )

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    import joblib

    joblib.dump(
        {"pipeline": pipeline, "classes": list(classes), "schema": 1},
        ARTIFACT_DIR / MODEL_FILENAME,
    )

    metadata = {
        "version": "v1",
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "algorithm": "tfidf(char_wb 2-4 + word 1-2) + multinomial logistic regression, "
        "calibrated (sigmoid, cv=3)",
        "hyperparameters": HYPERPARAMETERS,
        "train_split_sha256": _sha256(DATASET_DIR / "crisis_cases_train.jsonl"),
        "dev_split_sha256": _sha256(DATASET_DIR / "crisis_cases_dev.jsonl"),
        "train_cases": len(train_texts),
        "train_label_counts": {
            label: train_labels.count(label) for label in LEVEL_LABELS
        },
        "dev_metrics": {
            "cases": len(dev_texts),
            "accuracy": round(accuracy_score(dev_labels, dev_predictions), 4),
            "macro_f1": round(f1_score(dev_labels, dev_predictions, average="macro"), 4),
            "high_plus_imminent_recall": (
                round(crisis_recall, 4) if crisis_recall is not None else None
            ),
        },
        "test_split_used": False,
    }
    (ARTIFACT_DIR / METADATA_FILENAME).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(f"artifact written to {ARTIFACT_DIR}")


if __name__ == "__main__":
    main()
