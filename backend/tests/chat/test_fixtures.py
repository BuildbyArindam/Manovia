"""The synthetic messages the chat tests rely on really sit on the tiers they claim."""

from __future__ import annotations

import pytest

from app.services.safety.base import RiskLevel
from app.services.safety.rules import build_engine
from tests.chat.conftest import (
    HIGH_MESSAGE,
    IMMINENT_MESSAGE,
    MEDIUM_MESSAGE,
    NEUTRAL_MESSAGE,
    PII_MESSAGE,
    SAD_MESSAGE,
)


@pytest.mark.parametrize(
    ("text", "level"),
    [
        (NEUTRAL_MESSAGE, RiskLevel.NONE),
        (SAD_MESSAGE, RiskLevel.NONE),
        (PII_MESSAGE, RiskLevel.NONE),
        (MEDIUM_MESSAGE, RiskLevel.MEDIUM),
        (HIGH_MESSAGE, RiskLevel.HIGH),
        (IMMINENT_MESSAGE, RiskLevel.IMMINENT),
    ],
)
def test_fixture_messages_sit_on_their_tier(text: str, level: RiskLevel) -> None:
    assert build_engine().assess(text).level == level
