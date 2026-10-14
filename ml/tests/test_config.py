"""The config schema is the first safety rail: it must reject, not repair.

Every test here is a property someone relied on in production somewhere: an
unknown key is a typo that silently changes the model if it is ignored; a seed
outside 4 bytes is not the same seed on another machine; a hash that is not a
hash is not a pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from manovia_ml.config import (
    ConfigError,
    ExpectedMetrics,
    TrainConfig,
    compare_metrics,
    config_fingerprint,
    expected_metrics_path,
    load_config,
    load_config_dict,
    load_expected_metrics,
    write_expected_metrics,
)
from manovia_ml.paths import config_dir
from tests.conftest import config_dict

HASH = "a" * 64


def minimal() -> dict[str, Any]:
    return {
        "name": "unit",
        "seed": 1,
        "data": {
            "train": {"path": "data/processed/x_train.jsonl", "sha256": HASH, "rows": 2},
            "eval": {"path": "data/processed/x_eval.jsonl"},
        },
        "model": {"kind": "tfidf_sgd"},
    }


def test_minimal_config_applies_documented_defaults() -> None:
    config = load_config_dict(minimal())

    assert config.model.kind == "tfidf_sgd"
    assert config.evaluation.tolerance == 1.0e-6
    assert config.tracking.experiment == "manovia-ml"
    assert config.tracking.tracking_uri is None
    assert config.threads == 1
    assert config.data.labels is None


def test_unknown_key_is_an_error() -> None:
    raw = minimal()
    raw["model"]["max_iters"] = 10  # a typo for max_iter

    with pytest.raises(ConfigError) as excinfo:
        load_config_dict(raw)

    assert "max_iters" in str(excinfo.value)


def test_unknown_top_level_key_is_an_error() -> None:
    raw = minimal()
    raw["seed_a"] = 3

    with pytest.raises(ConfigError):
        load_config_dict(raw)


@pytest.mark.parametrize("seed", [-1, 2**32, 2**32 + 1])
def test_seed_must_fit_in_four_bytes(seed: int) -> None:
    raw = minimal()
    raw["seed"] = seed

    with pytest.raises(ConfigError):
        load_config_dict(raw)


def test_sha256_pin_must_be_a_hash() -> None:
    raw = minimal()
    raw["data"]["eval"] = {"path": "data/processed/x_eval.jsonl", "sha256": "not-a-hash"}

    with pytest.raises(ConfigError):
        load_config_dict(raw)


def test_sha256_pin_is_lowercased() -> None:
    raw = minimal()
    raw["data"]["train"]["sha256"] = HASH.upper()

    assert load_config_dict(raw).data.train.sha256 == HASH


def test_ngram_range_must_be_ordered() -> None:
    raw = minimal()
    raw["model"]["tfidf"] = {"word_ngram_range": [3, 1]}

    with pytest.raises(ConfigError):
        load_config_dict(raw)


def test_config_objects_are_frozen() -> None:
    config = load_config_dict(minimal())

    with pytest.raises(ValidationError):
        config.seed = 5  # type: ignore[misc]


def test_config_is_discriminated_by_kind() -> None:
    raw = minimal()
    raw["model"] = {"kind": "tfidf_logreg", "C": 0.5}

    config = load_config_dict(raw)

    assert config.model.kind == "tfidf_logreg"
    assert config.model.C == 0.5
    assert config.model.solver == "lbfgs"


def test_liblinear_is_not_offered_because_it_cannot_run() -> None:
    """scikit-learn 1.9 refuses `liblinear` for n_classes >= 3 (found by running it)."""

    raw = minimal()
    raw["model"] = {"kind": "tfidf_logreg", "solver": "liblinear"}

    with pytest.raises(ConfigError):
        load_config_dict(raw)


def test_fingerprint_is_stable_and_sensitive() -> None:
    first = load_config_dict(minimal())
    second = load_config_dict(minimal())
    changed = load_config_dict({**minimal(), "seed": 2})

    assert config_fingerprint(first) == config_fingerprint(second)
    assert config_fingerprint(first) != config_fingerprint(changed)
    assert len(config_fingerprint(first)) == 64


def test_load_config_resolves_names_against_the_config_directory() -> None:
    config = load_config("toy_emotion_sgd.yaml")

    assert isinstance(config, TrainConfig)
    assert config.name == "toy_emotion_sgd"
    assert config.data.train.path == "data/processed/toy_smoke_train.jsonl"


def test_load_config_missing_file_names_the_path() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config("does_not_exist.yaml")

    assert "config not found" in str(excinfo.value)


def test_load_config_rejects_a_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_rejects_broken_yaml(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("name: [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


def test_every_shipped_config_validates() -> None:
    """A config that does not parse is a run that cannot happen.

    Day 34 added two more config families beside ``TrainConfig``:
    distillation runs (``distill_*.yaml``) and the release gates
    (``release_gates.yaml``). Each file is validated by its own strict
    loader — unknown keys are still errors everywhere — and a file that
    matches no family fails here rather than being quietly skipped.
    """

    from manovia_ml.compression.gates import load_release_gates
    from manovia_ml.config import load_distill_config
    from manovia_ml.paths import config_dir

    configs = sorted(config_dir().glob("*.y*ml"))
    assert configs, "no configs found"
    for path in configs:
        if path.name == "release_gates.yaml":
            load_release_gates(path)
            continue
        if path.name.startswith("distill_"):
            assert load_distill_config(path).name, path.name
            continue
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        config = load_config_dict(parsed, origin=str(path))
        assert config.name, path.name


def test_expected_metrics_roundtrip(tmp_path: Path) -> None:
    config_path = tmp_path / "unit.yaml"
    config_path.write_text(yaml.safe_dump(minimal()), encoding="utf-8")
    expected = ExpectedMetrics(
        config="unit.yaml",
        seed=1,
        tolerance=1.0e-6,
        metrics={"accuracy": 0.5},
        run_id="abc",
    )

    written = write_expected_metrics(config_path, expected)
    loaded = load_expected_metrics(config_path)

    assert written == expected_metrics_path(config_path)
    assert loaded is not None
    assert loaded.metrics == {"accuracy": 0.5}


def test_a_temporary_config_keeps_its_baseline_beside_it(tmp_path: Path) -> None:
    """Regression: baselines must not be written into this repository.

    The path used to be derived from the config *name* and always resolved into
    `ml/configs/expected/`, so any run against a config stored elsewhere (the
    reproducibility tests, a Colab copy of a config, an experiment in /tmp) left
    files in the checkout. A baseline belongs next to the config that produced
    it, and `ml/configs/expected/` holds baselines of `ml/configs/` only.
    """

    config_path = tmp_path / "somewhere_else.yaml"
    config_path.write_text(yaml.safe_dump(minimal()), encoding="utf-8")

    written = write_expected_metrics(
        config_path,
        ExpectedMetrics(
            config="somewhere_else.yaml", seed=1, tolerance=1.0e-6, metrics={"accuracy": 0.5}
        ),
    )

    assert written == tmp_path / "expected" / "somewhere_else.json"
    assert written.is_file()
    assert not (config_dir() / "expected" / "somewhere_else.json").exists()


def test_configs_in_the_configs_directory_keep_their_baseline_convention() -> None:
    """The documented convention still holds for configs that live in ml/configs/."""

    path = expected_metrics_path("configs/toy_emotion_sgd.yaml")

    assert path == config_dir() / "expected" / "toy_emotion_sgd.json"
    assert path.is_file(), "the committed smoke baseline must be where runs look for it"


def test_compare_metrics_reports_only_real_differences() -> None:
    problems = compare_metrics(
        {"accuracy": 0.5, "macro_f1": 0.4000001},
        {"accuracy": 0.5, "macro_f1": 0.4},
        tolerance=1.0e-6,
    )

    assert problems == []


def test_compare_metrics_flags_drift_and_missing_metrics() -> None:
    problems = compare_metrics(
        {"accuracy": 0.9}, {"accuracy": 0.5, "macro_f1": 0.4}, tolerance=1.0e-6
    )

    assert len(problems) == 2
    assert any("accuracy" in problem for problem in problems)
    assert any("missing" in problem for problem in problems)


def test_toy_config_fixture_is_valid(toy_config: TrainConfig) -> None:
    assert toy_config.data.labels == ["joy", "anger"]
    assert toy_config.model.kind == "tfidf_sgd"


def test_config_dict_helper_rejects_unknown_kind() -> None:
    with pytest.raises(ConfigError):
        load_config_dict(config_dict({"train": {"path": "x"}, "eval": {"path": "y"}}, kind="nope"))
