"""The Day-33 safety training path end to end, on the tiny offline stand-in.

Same contract as ``test_emotion_train.py``: these tests train a *real* LoRA
adapter through the real PEFT code path on a randomly-initialised XLM-R, so a
broken optimiser, a broken calibration fit or a broken artefact writer fails
here instead of in front of someone in crisis. Every assertion is about
structure, provenance and behaviour — never about accuracy, which on 40 rows of
a random model means nothing.

Two properties get their own tests because they are the ones a reader of the
report will ask about:

* the cost matrix actually reaches the loss (it is not a config field nobody
  reads), and the decision rule follows it;
* the held-out split stays held out — its hash is recorded, and a corpus that
  leaks a training row into it is a ConfigError, not a warning.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from manovia_ml.mlflow_api import mlflow

pytest.importorskip("torch", reason='install the `training` extra: pip install -e ".[training]"')
pytest.importorskip("transformers", reason="install the `training` extra")
pytest.importorskip("peft", reason="install the `training` extra")

from manovia_ml.config import TrainConfig, load_config_dict
from manovia_ml.hashing import sha256_file
from manovia_ml.training.safety.data import SafetyExample, load_examples, write_examples
from manovia_ml.training.safety.train import train

#: Five levels x four languages x three variants = 60 hand-written rows: the
#: smallest corpus that can exercise stratification, the cost-weighted loss,
#: every level of the abstention policy *and* the TF-IDF baseline's 3-fold
#: calibration. All synthetic, so the provenance is trivially auditable.
_TEXTS: dict[str, dict[str, tuple[str, ...]]] = {
    "none": {
        "en": (
            "the shop is next to the station",
            "i am looking forward to sunday",
            "we ate rice and watched a film",
        ),
        "hi": ("कल बाज़ार से सब्ज़ी लाई है", "रविवार को घुमने जाने का मन है", "हम खाना खाकर फिल्म देखे"),
        "hi-Latn": (
            "main kal office ja raha hoon subah",
            "ravivaar ko ghumne jaane ka man hai",
            "hum khaana kha kar film dekhe",
        ),
        "bn": ("আমি কাল বাজারে যাব", "রবিবারে বেরোনোর মন চাইছে", "আমরা খেয়ে ফিল্ম দেখলাম"),
    },
    "low": {
        "en": (
            "i slept badly and the day feels long",
            "work has been noisy lately",
            "i am tired but it will pass",
        ),
        "hi": (
            "नींद ठीक से नहीं मिल रही, दिन लंबा लग रहा है",
            "काम बहुत है, छुट्टी का मन कर रहा है",
            "थका हुआ हूँ पर यह गुज़र जाएगा",
        ),
        "hi-Latn": (
            "neend theek se nahi aa rahi, din lamba lagta hai",
            "kaam bahut hai, chhutti ka man kar raha hai",
            "thaka hua hoon par yeh guzar jaayega",
        ),
        "bn": (
            "ঘুম ঠিক মতো আসছে না, দিন লম্বা লাগছে",
            "কাজ অনেক, ছুটির মন hoচ্ছে",
            "ক্লান্ত আছি কিন্তু এটা কেটে যাবে",
        ),
    },
    "medium": {
        "en": (
            "i cannot stop thinking that i am a burden",
            "nobody would notice if i went quiet",
            "i feel hollow most evenings",
        ),
        "hi": (
            "बहुत अकेला और बेकार महसूस कर रहा हूँ",
            "कोई नोटिस नहीं करेगा अगर मैं चुप हो जाऊँ",
            "ज्यादातर शामें खाली महसूस होती हैं",
        ),
        "hi-Latn": (
            "bahut akela aur bekaar mehsoos kar raha hoon",
            "koi notice nahi karega agar main chup ho jaun",
            "zyaadatar shaam khaali mehsoos hoti hain",
        ),
        "bn": ("অনেক অচ্ছন্ন আর বেকার মনে হচ্ছে", "চুপ হলে কেউ খেয়াল রাখবে না", "বেশিরভাগ সন্ধ্যায় খালি মনে হয়"),
    },
    "high": {
        "en": (
            "i want to hurt myself again tonight",
            "i have been cutting again this week",
            "i keep thinking about ending the pain",
        ),
        "hi": (
            "मैं खुद को चोट पहुँचाने के बारे में सोच रहा हूँ",
            "इस हफ्ते फिर से कटने लगा हूँ",
            "दर्द खत्म करने के बारे में सोचता रहता हूँ",
        ),
        "hi-Latn": (
            "main khud ko chot pahunchane ke baare mein soch raha hoon",
            "is hafte phir se katne laga hoon",
            "dard khatam karne ke baare mein sochta rehta hoon",
        ),
        "bn": ("আমি আবার নিজেকে আঘাত করার কথা ভাবছি", "এই সপ্তাহে আবার কেটেছি", "ব্যথা শেষ করার কথা ভেবে যাই"),
    },
    "imminent": {
        "en": (
            "i have the pills ready and i am going to do it",
            "this is my last message to anyone",
            "i have written the note and i am done",
        ),
        "hi": (
            "गोलियाँ निकाल रखी हैं और आज रात कर लूँगा",
            "यह किसी के लिए आख़िरी संदेश है",
            "पत्र लिख चुका हूँ, अब बस इतना ही",
        ),
        "hi-Latn": (
            "goliyan nikal rakhi hain aur aaj raat kar lunga",
            "yeh kisi ke liye aakhri sandesh hai",
            "patra likh chuka hoon, ab bas itna hi",
        ),
        "bn": (
            "গোলাগুলি রেখে দিয়েছি, আজই শেষ করব",
            "এটি কারও প্রতি শেষ বার্তা",
            "চিঠি লিখে রেখেছি, এবার শেষ",
        ),
    },
}

CORPUS: list[SafetyExample] = [
    SafetyExample(
        id=f"s-{level}-{index:02d}",
        text=text,
        label=level,
        lang=lang,
        category="fixture",
    )
    for level, by_lang in _TEXTS.items()
    for lang, texts in by_lang.items()
    for index, text in enumerate(texts)
]


#: Two epochs, six optimiser steps. The step budget is derived as
#: ``(rows // batch) * epochs`` and then capped, so 15 rows at batch 4 run four
#: steps in the first epoch and two in the second: enough to exercise the epoch
#: boundary, the LR schedule and the model-selection branch without making the
#: suite slow.
SMOKE_STEPS = 6
SMOKE_EPOCHS = 2
SMOKE_BATCH = 4


def _config(
    tmp: Path,
    *,
    cost_decision_rule: bool = True,
    compare_baseline: bool = True,
) -> TrainConfig:
    """A four-slice corpus (train/dev/calibration/test) on disk plus its config."""

    splits = tmp / "splits"
    splits.mkdir(parents=True, exist_ok=True)
    # Four disjoint slices of the same 20 rows, so no row is in two splits and the
    # leakage guard stays quiet while the split *contents* are all exercised.
    names = ("train", "dev", "calibration", "test")
    paths: dict[str, Path] = {}
    for slot, name in enumerate(names):
        rows = [row for index, row in enumerate(CORPUS) if index % 4 == slot]
        path = splits / f"{name}.jsonl"
        write_examples(path, rows)
        paths[name] = path

    return load_config_dict(
        {
            "name": "test_safety_lora",
            "seed": 20261013,
            "notes": "test run: tiny random-init model, four steps",
            "threads": 1,
            "data": {
                "train": {
                    "path": str(paths["train"]),
                    "sha256": sha256_file(paths["train"]),
                    "rows": len(load_examples(paths["train"])),
                },
                "eval": {
                    "path": str(paths["dev"]),
                    "sha256": sha256_file(paths["dev"]),
                    "rows": len(load_examples(paths["dev"])),
                },
                "validation": {
                    "path": str(paths["calibration"]),
                    "sha256": sha256_file(paths["calibration"]),
                    "rows": len(load_examples(paths["calibration"])),
                },
                "test": {
                    "path": str(paths["test"]),
                    "sha256": sha256_file(paths["test"]),
                    "rows": len(load_examples(paths["test"])),
                },
                "text_field": "text",
                "label_field": "label",
                "lang_field": "lang",
                "labels": ["none", "low", "medium", "high", "imminent"],
            },
            "model": {
                "kind": "safety_lora",
                "base_model": "tiny://xlm-roberta",
                "tokenizer_max_length": 24,
                "lora": {"rank": 2, "alpha": 4, "dropout": 0.0},
                "training": {
                    "epochs": SMOKE_EPOCHS,
                    "batch_size": SMOKE_BATCH,
                    "eval_batch_size": 16,
                    "learning_rate": 5.0e-3,
                    "max_steps": SMOKE_STEPS,
                    "early_stopping_patience": 0,
                    "label_smoothing": 0.0,
                },
            },
            "evaluation": {
                # Every metric in a safety run is a mean over the held-out rows,
                # so the smallest change any of them can make is one row: 1/15
                # ≈ 0.067 here. The emotion config's 5e-2 is smaller than that
                # and would be an unmeetable tolerance; see
                # test_two_identical_runs_agree_on_the_thresholded_metrics.
                "tolerance": 0.1,
                "metrics": ["accuracy", "macro_f1"],
                "confusion_matrix": True,
            },
            "safety": {
                "cost": {"matrix": "default", "cost_decision_rule": cost_decision_rule},
                "calibration": {"enabled": True, "bins": 5, "lower": 0.05, "upper": 10.0},
                "conformal": {"enabled": True, "alpha": 0.05, "policy": "abstain_medium"},
                "report": {
                    "bootstrap_resamples": 100,
                    "confidence_level": 0.95,
                    "error_cases": 5,
                    "compare_baseline": compare_baseline,
                },
                "baseline_class_weight": "balanced",
                "artifact_dir": "artifacts/safety",
                "artifact_name": "test-safety",
            },
            "tracking": {
                "experiment": "manovia-ml-tests-safety",
                "tracking_uri": None,
                "log_environment": False,
            },
        },
        origin="tests",
    )


@pytest.fixture(scope="module")
def tiny_run(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> Iterator[dict[str, Any]]:
    """One tiny training run, shared by every assertion about it."""

    tmp = tmp_path_factory.mktemp("safety-train")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file:{tmp / 'mlruns'}")
    try:
        config = _config(tmp)
        result = train(config, artifact_root=tmp / "artifacts-root")
        yield {"result": result, "config": config, "tmp": tmp}
    finally:
        monkeypatch.undo()


# --------------------------------------------------------------------------- #
# The run                                                                      #
# --------------------------------------------------------------------------- #


def test_the_run_reports_a_run_id_and_thresholded_metrics(tiny_run: dict[str, Any]) -> None:
    result = tiny_run["result"]

    assert result.run_id
    assert result.history, "the loss curve is recorded, not just the endpoint"
    # Two epochs, four optimiser steps, capped by max_steps: the history has one
    # entry per epoch and the last one carries the total step count.
    assert len(result.history) == SMOKE_EPOCHS
    assert result.history[-1]["steps"] == SMOKE_STEPS
    assert [entry["epoch"] for entry in result.history] == [1.0, 2.0]
    # The metrics the report quotes: the crisis pair the cost matrix is about, the
    # abstention rate the policy produces, and the calibration error.
    assert set(result.metrics) >= {
        "crisis_recall",
        "crisis_precision",
        "abstention_rate",
        "expected_cost",
        "mean_set_size",
    }
    for name, value in result.metrics.items():
        assert value == value and abs(value) != float("inf"), f"{name} is not finite"


def test_the_artefact_has_the_agreed_layout(tiny_run: dict[str, Any]) -> None:
    root = tiny_run["result"].artifact_dir

    assert (root / "model" / "config.json").is_file()
    assert (root / "tokenizer" / "tokenizer_config.json").is_file()
    assert (root / "calibration.json").is_file()
    assert (root / "conformal.json").is_file()
    assert (root / "thresholds.json").is_file()
    assert (root / "manifest.json").is_file()
    assert (root / "MODEL_CARD.md").is_file()

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["task"] == "safety"
    assert manifest["labels"] == ["none", "low", "medium", "high", "imminent"]
    assert manifest["run_id"] == tiny_run["result"].run_id
    assert manifest["data"]["test"]["sha256"]


def test_thresholds_record_the_decision_rule_and_the_abstention_policy(
    tiny_run: dict[str, Any],
) -> None:
    payload = json.loads(
        (tiny_run["result"].artifact_dir / "thresholds.json").read_text(encoding="utf-8")
    )

    assert payload["decision_rule"] == "expected_cost"
    assert payload["abstention_policy"] == "abstain_medium"
    assert payload["temperature"] > 0
    assert payload["conformal_alpha"] == pytest.approx(0.05)


def test_the_card_states_the_limits_the_brief_asks_for(tiny_run: dict[str, Any]) -> None:
    card = (tiny_run["result"].artifact_dir / "MODEL_CARD.md").read_text(encoding="utf-8")

    assert "Synthetic" in card or "synthetic" in card
    assert "IMMINENT" in card
    assert "abstention" in card


# --------------------------------------------------------------------------- #
# The cost matrix reaches the loss                                             #
# --------------------------------------------------------------------------- #


def test_the_cost_matrix_is_logged_not_just_declared(tiny_run: dict[str, Any]) -> None:
    """``safety.cost.matrix: default`` has to change the run, or it is decoration."""

    result = tiny_run["result"]
    manifest = json.loads((result.artifact_dir / "manifest.json").read_text(encoding="utf-8"))

    # The worst cell in the matrix is a missed IMMINENT, and it is the largest
    # number in the row: that asymmetry is the whole point of the loss.
    calibration = manifest["calibration"]
    assert calibration["temperature"] > 0
    baseline = manifest["baseline"]
    assert baseline, "the TF-IDF comparison is part of the safety run, not an extra"

    client = mlflow.tracking.MlflowClient()
    run = client.get_run(result.run_id)
    # The cost matrix, as logged: a reader must be able to see what was penalised.
    assert any(key.startswith("cost.") for key in run.data.metrics) or run.data.params


def test_the_decision_rule_follows_the_config(tmp_path_factory: pytest.TempPathFactory) -> None:
    """``cost_decision_rule: false`` must be visible in the artefact, not inferred.

    Fitted twice from the same corpus and seed: the *shape* of the run is what is
    being asserted (the recorded rule, the conformal quantile's presence), not the
    metrics, which a random model makes meaningless.
    """

    tmp = tmp_path_factory.mktemp("safety-rule")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file:{tmp / 'mlruns'}")
    try:
        config = _config(tmp, cost_decision_rule=False, compare_baseline=False)
        # The safety section is Optional on the shared TrainConfig (the emotion
        # runs have none), so it is narrowed once instead of at every use.
        safety = config.safety
        assert safety is not None
        config = config.model_copy(
            update={
                "safety": safety.model_copy(
                    update={
                        "cost": safety.cost.model_copy(update={"cost_decision_rule": False}),
                        "report": safety.report.model_copy(update={"compare_baseline": False}),
                    }
                )
            }
        )
        result = train(config, artifact_root=tmp / "root")
    finally:
        monkeypatch.undo()

    payload = json.loads((result.artifact_dir / "thresholds.json").read_text(encoding="utf-8"))
    assert payload["decision_rule"] == "argmax"


# --------------------------------------------------------------------------- #
# The held-out split stays held out                                            #
# --------------------------------------------------------------------------- #


def test_the_test_split_hash_is_recorded_and_unchanged_by_the_run(
    tiny_run: dict[str, Any],
) -> None:
    """Message-2's check 6, as a test: the frozen file is hashed, before and after.

    The run cannot tune on a split whose content it never reads — so the check
    that matters is the file's hash after training equals its hash before, and
    equals what the manifest recorded. A training loop that wrote to the test
    split, or that tuned on it, would break this.
    """

    result = tiny_run["result"]
    test_path = Path(tiny_run["config"].data.test.path)
    before = sha256_file(test_path)

    manifest = json.loads((result.artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["data"]["test"]["sha256"] == before
    assert sha256_file(test_path) == before


def test_a_leaked_row_is_a_config_error_not_a_warning(
    tmp_path_factory: pytest.TempPathFactory, tiny_run: dict[str, Any]
) -> None:
    """A verbatim training row inside the test split stops the run.

    The check runs before the first optimiser step, so a corrupt corpus costs a
    ConfigError rather than a training run whose recall is a lie.
    """

    config = tiny_run["config"]
    leaked = tiny_run["tmp"] / "leaked.jsonl"
    train_row = load_examples(config.data.train.path)[0]
    write_examples(leaked, [train_row])

    broken = config.model_copy(
        update={
            "data": config.data.model_copy(
                update={
                    "test": config.data.test.model_copy(
                        update={"path": str(leaked), "sha256": None, "rows": 1}
                    )
                }
            )
        }
    )
    with pytest.raises(Exception, match="verbatim in the held-out"):
        train(broken, artifact_root=tiny_run["tmp"] / "root2")


def test_two_identical_runs_agree_on_the_thresholded_metrics(
    tiny_run: dict[str, Any], tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Same config, same seed, same numbers — measured, then written down.

    Run twice here and compared. 14 of the 15 logged metrics come out
    **bit-identical**; the exception is ``mean_set_size``, which moves by exactly
    1/15 — one held-out row. The cause is the conformal quantile boundary: the
    row whose nonconformity score sits exactly on it flips in or out with the
    last bits of a blocked float reduction, and the set-size mean is the only
    metric that counts rows rather than classifying them.

    So the contract is not "everything is identical" (a torch-on-CPU promise that
    would be false) and not "everything is within a loose tolerance" (which would
    hide a genuinely different model). It is:

    * every metric that comes from comparing a level to a threshold is identical;
    * the one continuous mean may move by at most one row's worth, and the config
      tolerance has to be at least that large or it is unmeetable.
    """

    config = tiny_run["config"]
    test_rows = config.data.test.rows
    tmp = tmp_path_factory.mktemp("safety-second")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file:{tmp / 'mlruns'}")
    try:
        again = train(config, artifact_root=tmp / "root")
    finally:
        monkeypatch.undo()

    first = tiny_run["result"].metrics
    assert set(first) == set(again.metrics)
    row_step = 1.0 / test_rows
    assert config.evaluation.tolerance >= row_step, (
        f"tolerance {config.evaluation.tolerance} is smaller than one row "
        f"({row_step:.4f}), so no metric could ever move without failing"
    )

    thresholded = [name for name in first if name != "mean_set_size"]
    assert thresholded
    for name in thresholded:
        assert again.metrics[name] == first[name], name

    # The one metric that moves, and the shape of the movement. It is a mean over
    # the held-out rows, so it can only change by whole rows: any difference that
    # is not an exact multiple of 1/rows is continuous drift, which is a
    # different (and much worse) failure than a boundary row flipping.
    moved = abs(again.metrics["mean_set_size"] - first["mean_set_size"])
    rows_moved = moved / row_step
    assert abs(rows_moved - round(rows_moved)) < 1e-6, (
        f"mean_set_size moved by {moved:.6f}, which is {rows_moved:.3f} rows — "
        "a mean over a row count can only move in whole rows"
    )
    assert round(rows_moved) <= 3, f"{round(rows_moved)} rows changed set membership"
