from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.services.plan.policy.reward import (
    DEFAULT_ABANDONMENT_PENALTY,
    bounded_improvement_reward,
)


def test_reward_is_normalized_pre_post_rating_improvement() -> None:
    assert bounded_improvement_reward(1, 5, started=True) == 1.0
    assert bounded_improvement_reward(5, 1, started=True) == -1.0
    assert bounded_improvement_reward(2, 4, started=True) == 0.5
    assert bounded_improvement_reward(3, 3, started=True) == 0.0


def test_skip_and_never_started_are_missing_not_failure() -> None:
    assert bounded_improvement_reward(1, None, started=False) is None
    assert bounded_improvement_reward(1, None, started=True, skipped=True) is None


def test_abandonment_receives_only_the_small_bounded_penalty_when_no_post_rating() -> None:
    assert (
        bounded_improvement_reward(3, None, started=True, abandoned=True)
        == -DEFAULT_ABANDONMENT_PENALTY
    )


def test_abandonment_penalty_is_applied_after_rating_improvement() -> None:
    reward = bounded_improvement_reward(
        2,
        4,
        started=True,
        abandoned=True,
        abandonment_penalty=0.05,
    )
    assert reward == pytest.approx(0.45)


def test_reward_rejects_out_of_scale_rating_and_large_penalty() -> None:
    with pytest.raises(ValueError, match="pre_rating"):
        bounded_improvement_reward(0, 4, started=True)
    with pytest.raises(ValueError, match="post_rating"):
        bounded_improvement_reward(2, 6, started=True)
    with pytest.raises(ValueError, match="abandonment_penalty"):
        bounded_improvement_reward(2, 3, started=True, abandonment_penalty=0.3)


@given(
    pre=st.integers(min_value=1, max_value=5),
    post=st.integers(min_value=1, max_value=5),
    penalty=st.floats(min_value=0.0, max_value=0.2, allow_nan=False, allow_infinity=False),
)
def test_property_rating_reward_is_always_bounded(pre: int, post: int, penalty: float) -> None:
    reward = bounded_improvement_reward(
        pre,
        post,
        started=True,
        abandoned=True,
        abandonment_penalty=penalty,
    )
    assert reward is not None
    assert -1.0 <= reward <= 1.0


def test_skip_never_updates_a_posterior() -> None:
    from app.services.plan.policy.bandits import ContextualBandit
    from app.services.plan.policy.features import InterventionContext
    from app.services.plan.policy.safety import SafeInterventionService

    engine = ContextualBandit(seed=1)
    service = SafeInterventionService(engine)
    reward = service.record_outcome(
        "internal-user",
        InterventionContext(local_hour=10),
        "body-scan",
        pre_rating=3,
        post_rating=None,
        started=True,
        skipped=True,
    )
    assert reward is None
    assert engine.population_observations == 0
    assert engine.observations_for("internal-user", "body-scan") == 0
