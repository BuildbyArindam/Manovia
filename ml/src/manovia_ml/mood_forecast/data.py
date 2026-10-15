"""Leakage-resistant temporal/user splits and fixed 28-day sequence windows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast

import numpy as np

from manovia_ml.mood_forecast.config import MoodForecastConfig
from manovia_ml.mood_forecast.synthetic import FACTOR_TAGS, MoodPanel, MoodSeries

LOOKBACK_DAYS: Final = 28
HORIZON_DAYS: Final = 3
FEATURE_NAMES: Final[tuple[str, ...]] = (
    "valence",
    "energy",
    "sleep_tag",
    "exercise_done",
    "observed",
    *(f"day_of_week_{day}" for day in range(7)),
    *(f"factor_{tag}" for tag in FACTOR_TAGS),
)
FEATURE_INDEX: Final = {name: index for index, name in enumerate(FEATURE_NAMES)}
FEATURE_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "context": (
        "sleep_tag",
        "exercise_done",
        *(f"factor_{tag}" for tag in FACTOR_TAGS),
    ),
    "day_of_week": tuple(f"day_of_week_{day}" for day in range(7)),
    "energy": ("energy",),
}


@dataclass(frozen=True)
class WindowSample:
    """A forecast made at ``origin_index`` using only its preceding 28 days."""

    user_id: str
    origin_index: int
    features: np.ndarray
    targets: np.ndarray
    target_mask: np.ndarray
    target_days: tuple[str, ...]
    mase_scale: float


@dataclass(frozen=True)
class UserSplit:
    """Stable user groups and integer temporal cut points."""

    train_users: tuple[str, ...]
    calibration_users: tuple[str, ...]
    test_users: tuple[str, ...]
    fit_cut: int
    train_cut: int
    calibration_cut: int


@dataclass(frozen=True)
class MoodWindows:
    """All fit, calibration, few-shot support and frozen test windows."""

    split: UserSplit
    train: tuple[WindowSample, ...]
    validation: tuple[WindowSample, ...]
    calibration: tuple[WindowSample, ...]
    personalization_support: tuple[WindowSample, ...]
    test: tuple[WindowSample, ...]


def _feature_row(series: MoodSeries, index: int) -> np.ndarray:
    day = series.days[index]
    values: np.ndarray = np.zeros(len(FEATURE_NAMES), dtype=np.float32)
    if day.observed:
        assert day.valence is not None
        assert day.energy is not None
        values[FEATURE_INDEX["valence"]] = (day.valence - 3.0) / 2.0
        values[FEATURE_INDEX["energy"]] = (day.energy - 3.0) / 2.0
        values[FEATURE_INDEX["sleep_tag"]] = float(bool(day.sleep_tag))
        values[FEATURE_INDEX["exercise_done"]] = float(bool(day.exercise_done))
        values[FEATURE_INDEX["observed"]] = 1.0
        for factor in day.factors:
            name = f"factor_{factor}"
            if name in FEATURE_INDEX:
                values[FEATURE_INDEX[name]] = 1.0
    weekday = day.day.weekday()
    values[FEATURE_INDEX[f"day_of_week_{weekday}"]] = 1.0
    return values


def _seasonal_scale(features: np.ndarray) -> float:
    """Training-only seasonal-naive scale available at the forecast origin."""
    observed = features[:, FEATURE_INDEX["observed"]] > 0.5
    valence = 3.0 + 2.0 * features[:, FEATURE_INDEX["valence"]]
    errors = [
        abs(float(valence[index] - valence[index - 7]))
        for index in range(7, len(features))
        if observed[index] and observed[index - 7]
    ]
    return max(float(np.mean(errors)) if errors else 0.0, 0.1)


def make_sample(series: MoodSeries, origin_index: int) -> WindowSample:
    """Create one input/target pair. ``origin_index`` is the last input day."""
    if origin_index < LOOKBACK_DAYS - 1 or origin_index + HORIZON_DAYS >= len(series.days):
        raise ValueError("origin does not leave exactly 28 input days and 3 forecast days")
    feature_rows = np.stack(
        [
            _feature_row(series, index)
            for index in range(origin_index - LOOKBACK_DAYS + 1, origin_index + 1)
        ]
    ).astype(np.float32)
    future = series.days[origin_index + 1 : origin_index + HORIZON_DAYS + 1]
    mask = np.asarray([day.observed for day in future], dtype=bool)
    targets = np.asarray(
        [float(day.valence) if day.observed and day.valence is not None else 0.0 for day in future],
        dtype=np.float32,
    )
    return WindowSample(
        user_id=series.user_id,
        origin_index=origin_index,
        features=feature_rows,
        targets=targets,
        target_mask=mask,
        target_days=tuple(day.day.isoformat() for day in future),
        mase_scale=_seasonal_scale(feature_rows),
    )


def _samples(
    series: MoodSeries,
    *,
    target_start_min: int,
    target_end_exclusive_max: int,
) -> tuple[WindowSample, ...]:
    results: list[WindowSample] = []
    # A forecast at t predicts t+1..t+3. Keep the complete target horizon in
    # its assigned chronological segment; missing labels are masked, not filled.
    for origin in range(LOOKBACK_DAYS - 1, len(series.days) - HORIZON_DAYS):
        target_start = origin + 1
        target_end_exclusive = origin + HORIZON_DAYS + 1
        if target_start < target_start_min or target_end_exclusive > target_end_exclusive_max:
            continue
        sample = make_sample(series, origin)
        if sample.target_mask.any():
            results.append(sample)
    return tuple(results)


def build_mood_windows(panel: MoodPanel, config: MoodForecastConfig) -> MoodWindows:
    """Build user-disjoint, forward-only windows with no target overlap.

    The training cohort is split again by time into a fit prefix and a later
    model-selection block. Separate calibration and test cohorts are reserved;
    test-user support windows are earlier than every frozen test target and are
    used only for the explicitly reported few-shot personalization experiment.
    """
    user_count = len(panel.users)
    train_count = int(user_count * config.split.train_users_fraction)
    calibration_count = int(user_count * config.split.calibration_users_fraction)
    test_count = user_count - train_count - calibration_count
    if min(train_count, calibration_count, test_count) < 1:
        raise ValueError("each of train, calibration and test needs at least one user")

    permutation = np.random.default_rng(config.seed).permutation(user_count)
    identifiers = [panel.users[int(index)].user_id for index in permutation]
    train_ids = tuple(sorted(identifiers[:train_count]))
    calibration_ids = tuple(sorted(identifiers[train_count : train_count + calibration_count]))
    test_ids = tuple(sorted(identifiers[train_count + calibration_count :]))

    total_days = len(panel.users[0].days) if panel.users else 0
    train_cut = int(total_days * config.split.train_time_fraction)
    calibration_cut = int(total_days * config.split.calibration_time_fraction)
    fit_cut = int(train_cut * config.split.train_fit_fraction)
    if fit_cut <= LOOKBACK_DAYS + HORIZON_DAYS or train_cut <= fit_cut + HORIZON_DAYS:
        raise ValueError("temporal split is too short for a fit and validation window")
    if calibration_cut <= train_cut + HORIZON_DAYS:
        raise ValueError("calibration time segment is too short")

    by_id = {user.user_id: user for user in panel.users}
    train: list[WindowSample] = []
    validation: list[WindowSample] = []
    calibration: list[WindowSample] = []
    support: list[WindowSample] = []
    test: list[WindowSample] = []
    for user_id in train_ids:
        user = by_id[user_id]
        train.extend(_samples(user, target_start_min=0, target_end_exclusive_max=fit_cut))
        validation.extend(
            _samples(user, target_start_min=fit_cut, target_end_exclusive_max=train_cut)
        )
    for user_id in calibration_ids:
        user = by_id[user_id]
        calibration.extend(
            _samples(user, target_start_min=train_cut, target_end_exclusive_max=calibration_cut)
        )
    for user_id in test_ids:
        user = by_id[user_id]
        support.extend(
            _samples(user, target_start_min=train_cut, target_end_exclusive_max=calibration_cut)
        )
        test.extend(
            _samples(user, target_start_min=calibration_cut, target_end_exclusive_max=total_days)
        )

    if not train or not validation or not calibration or not test:
        raise ValueError(
            "empty time split: need longer histories or more logging; "
            f"train={len(train)}, validation={len(validation)}, "
            f"calibration={len(calibration)}, test={len(test)}"
        )
    split = UserSplit(
        train_users=train_ids,
        calibration_users=calibration_ids,
        test_users=test_ids,
        fit_cut=fit_cut,
        train_cut=train_cut,
        calibration_cut=calibration_cut,
    )
    result = MoodWindows(
        split=split,
        train=tuple(train),
        validation=tuple(validation),
        calibration=tuple(calibration),
        personalization_support=tuple(support),
        test=tuple(test),
    )
    assert_temporal_isolation(result)
    return result


def assert_temporal_isolation(windows: MoodWindows) -> None:
    """Fail closed if users or target dates cross a protected split boundary."""
    split = windows.split
    groups = [set(split.train_users), set(split.calibration_users), set(split.test_users)]
    if groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2]:
        raise ValueError("user IDs overlap across train, calibration and test")
    if any(sample.user_id not in groups[0] for sample in (*windows.train, *windows.validation)):
        raise ValueError("fit or validation sample is outside the training user group")
    if any(sample.user_id not in groups[1] for sample in windows.calibration):
        raise ValueError("calibration sample is outside the calibration user group")
    if any(
        sample.user_id not in groups[2]
        for sample in (*windows.personalization_support, *windows.test)
    ):
        raise ValueError("test support or test sample is outside the held-out user group")
    for sample in windows.train:
        if sample.origin_index + HORIZON_DAYS >= split.fit_cut:
            raise ValueError("training target reaches the model-selection boundary")
    for sample in windows.validation:
        if (
            sample.origin_index + 1 < split.fit_cut
            or sample.origin_index + HORIZON_DAYS >= split.train_cut
        ):
            raise ValueError("validation target crosses the training time boundary")
    for sample in windows.calibration:
        if (
            sample.origin_index + 1 < split.train_cut
            or sample.origin_index + HORIZON_DAYS >= split.calibration_cut
        ):
            raise ValueError("calibration target crosses the calibration boundary")
    for sample in windows.personalization_support:
        if (
            sample.origin_index + 1 < split.train_cut
            or sample.origin_index + HORIZON_DAYS >= split.calibration_cut
        ):
            raise ValueError("personalization support target crosses its boundary")
    for sample in windows.test:
        if sample.origin_index + 1 < split.calibration_cut:
            raise ValueError("test target predates the frozen test interval")


def ablate_features(features: np.ndarray, groups: tuple[str, ...]) -> np.ndarray:
    """Return a copy with selected input groups set to their neutral baseline."""
    ablated = np.array(features, dtype=np.float32, copy=True)
    unknown = set(groups) - FEATURE_GROUPS.keys()
    if unknown:
        raise ValueError(f"unknown feature ablation group(s): {sorted(unknown)}")
    for group in groups:
        for name in FEATURE_GROUPS[group]:
            ablated[..., FEATURE_INDEX[name]] = 0.0
    return cast(np.ndarray, ablated)


__all__ = [
    "FEATURE_GROUPS",
    "FEATURE_INDEX",
    "FEATURE_NAMES",
    "HORIZON_DAYS",
    "LOOKBACK_DAYS",
    "MoodWindows",
    "UserSplit",
    "WindowSample",
    "ablate_features",
    "assert_temporal_isolation",
    "build_mood_windows",
    "make_sample",
]
