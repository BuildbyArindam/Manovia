"""Bounded, rating-based outcome; skipping is missing data, never a failure."""

from __future__ import annotations

from typing import Final

MIN_RATING: Final[int] = 1
MAX_RATING: Final[int] = 5
DEFAULT_ABANDONMENT_PENALTY: Final[float] = 0.1
MAX_ABANDONMENT_PENALTY: Final[float] = 0.2


def bounded_improvement_reward(
    pre_rating: int | None,
    post_rating: int | None,
    *,
    started: bool,
    abandoned: bool = False,
    skipped: bool = False,
    abandonment_penalty: float = DEFAULT_ABANDONMENT_PENALTY,
) -> float | None:
    """Return normalized pre/post improvement in ``[-1, 1]`` or no outcome.

    Ratings use the existing 1-5 personal scale. A started-but-abandoned
    session with no post rating receives only the small configured penalty;
    it is not assigned a fabricated rating. A skipped or never-started action
    returns ``None`` and must not update a posterior. No engagement signal is
    accepted by this function.
    """

    if not 0.0 <= abandonment_penalty <= MAX_ABANDONMENT_PENALTY:
        raise ValueError("abandonment_penalty must be in [0, 0.2]")
    if pre_rating is not None and not MIN_RATING <= pre_rating <= MAX_RATING:
        raise ValueError("pre_rating must be None or an integer in 1..5")
    if post_rating is not None and not MIN_RATING <= post_rating <= MAX_RATING:
        raise ValueError("post_rating must be None or an integer in 1..5")
    if skipped or not started:
        return None
    if abandoned and post_rating is None:
        return -abandonment_penalty
    if pre_rating is None or post_rating is None:
        return -abandonment_penalty if abandoned else None

    improvement = (post_rating - pre_rating) / (MAX_RATING - MIN_RATING)
    reward = improvement - (abandonment_penalty if abandoned else 0.0)
    return max(-1.0, min(1.0, reward))
