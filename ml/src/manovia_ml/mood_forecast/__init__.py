"""Research-only synthetic mood forecasting stack (Day 36).

Nothing in this package is loaded by the backend. The opt-in product Outlook
uses a deterministic personal-history baseline; learned sequence models remain
research-only until a separate model-serving review approves an artifact.
"""

from manovia_ml.mood_forecast.synthetic import (
    MoodDay,
    MoodPanel,
    MoodSeries,
    UserMoodParameters,
    generate_synthetic_panel,
)

__all__ = [
    "MoodDay",
    "MoodPanel",
    "MoodSeries",
    "UserMoodParameters",
    "generate_synthetic_panel",
]
