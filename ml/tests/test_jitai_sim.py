from __future__ import annotations

from manovia_ml.simulation.jitai import run_online_seed, run_ope_seed
from manovia_ml.simulation.jitai_config import JITAIConfig, load_jitai_config


def _config() -> JITAIConfig:
    return load_jitai_config("configs/jitai_smoke.yaml")


def test_jitai_online_simulation_is_deterministic_for_a_seed() -> None:
    config = _config()
    first = run_online_seed(config, "epsilon_greedy", 3701)
    second = run_online_seed(config, "epsilon_greedy", 3701)
    assert first == second


def test_learned_policy_regret_decreases_after_warmup() -> None:
    config = _config().model_copy(
        update={
            "online": _config().online.model_copy(
                update={"users_per_group": 3, "steps_per_user": 60, "context_jitter": 0}
            )
        }
    )
    for policy in ("epsilon_greedy", "linucb", "thompson_sampling"):
        result = run_online_seed(config, policy, 3701)
        assert result.late_mean_regret < result.early_mean_regret, (
            policy,
            result.early_mean_regret,
            result.late_mean_regret,
        )


def test_logged_data_ips_and_doubly_robust_are_close_to_known_truth() -> None:
    base = _config()
    config = base.model_copy(
        update={
            "offline": base.offline.model_copy(
                update={
                    "users_per_group": 8,
                    "train_events_per_user": 120,
                    "evaluation_events_per_user": 200,
                }
            )
        }
    )
    result = run_ope_seed(config, 3702)
    assert result.samples == 8 * 4 * 200
    assert abs(result.ips - result.ground_truth) < 0.15
    assert abs(result.doubly_robust - result.ground_truth) < 0.15
