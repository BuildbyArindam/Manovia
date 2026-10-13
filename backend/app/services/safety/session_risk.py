"""Session-level risk: escalate when a conversation adds up, not just a message.

Day 29 red-team finding RT-S-MT-01: a person can disclose risk *gradually* —
"I'm letting everyone down", "nothing I do matters", "what's the point", "I
won't need my stuff anymore", "I wrote letters to my family, just in case" —
and no single message is a crisis on its own. The per-message engine is
right about every one of them and the conversation as a whole is still a
crisis. This module is the deterministic answer: a small, explainable score
over the recent turns of one session.

Rules (ADR 0030):

* Only the **current** message can trigger a raise, and only when it carries
  some risk of its own (``LOW`` or ``MEDIUM``). A neutral message after a hard
  run ("ok, what should I cook?") is never escalated — that would be nagging.
* Points: ``LOW`` = 1, ``MEDIUM`` = 2, for the current message and for each
  stored user turn in the window. The window is the last
  :data:`WINDOW_USER_TURNS` user turns, and it **starts after the most recent
  crisis turn**: once the crisis card has been shown, the person is not
  escalated again for what they already said.
* Score ≥ :data:`MEDIUM_AT` → at least ``MEDIUM`` (a gentle check-in);
  score ≥ :data:`HIGH_AT` → ``HIGH`` (the deterministic crisis response).
* Anti-nagging: a raise to ``MEDIUM`` happens at most once per window — if a
  check-in turn (stored tier ELEVATED) is already in the window, only a raise
  to ``HIGH`` is possible.
* A message about somebody else (third person, fiction) is never raised: the
  accumulated evidence is the speaker's own trajectory, and the raised reply
  addresses the speaker.

The raise is recorded as a rationale code (``session.accumulated_medium`` /
``session.accumulated_high``) so it is visible in logs and safety events —
codes, never text.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Protocol

from app.services.safety.base import RiskAssessment, RiskLevel

#: How many recent *user* turns (including the current one) are scored.
WINDOW_USER_TURNS: Final = 8
#: Score thresholds.
MEDIUM_AT: Final = 3
HIGH_AT: Final = 6

CODE_ACCUMULATED_MEDIUM: Final = "session.accumulated_medium"
CODE_ACCUMULATED_HIGH: Final = "session.accumulated_high"

#: Stored four-tier risk (``app.models.enums.RiskLevel``) of a past turn.
_STORED_CAUTION: Final = 1
_STORED_ELEVATED: Final = 2
_STORED_CRISIS: Final = 3

_POINTS_FOR_STORED: Final[dict[int, int]] = {_STORED_CAUTION: 1, _STORED_ELEVATED: 2}
_POINTS_FOR_CURRENT: Final[dict[RiskLevel, int]] = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2}


class ScoredTurn(Protocol):
    """What the accumulator needs from a stored turn (``HistoryTurn`` fits)."""

    @property
    def role(self) -> str: ...

    @property
    def risk(self) -> int: ...


def session_score(current: RiskLevel, history: Sequence[ScoredTurn]) -> tuple[int, bool]:
    """Return ``(score, check_in_already_given)`` for the current message.

    ``history`` is oldest first and does not include the current message.
    """
    score = _POINTS_FOR_CURRENT.get(current, 0)
    checked_in = False
    counted = 1
    for turn in reversed(history):
        if turn.role != "user":
            continue
        if turn.risk >= _STORED_CRISIS:
            break
        if counted >= WINDOW_USER_TURNS:
            break
        score += _POINTS_FOR_STORED.get(turn.risk, 0)
        checked_in = checked_in or turn.risk == _STORED_ELEVATED
        counted += 1
    return score, checked_in


def accumulate(assessment: RiskAssessment, history: Sequence[ScoredTurn]) -> RiskAssessment:
    """Return ``assessment``, raised if the session adds up (never lowered)."""
    level = assessment.level
    if level not in _POINTS_FOR_CURRENT:
        return assessment
    if assessment.context.third_person or assessment.context.fiction_frame:
        return assessment

    score, checked_in = session_score(level, history)
    if score >= HIGH_AT:
        target, code = RiskLevel.HIGH, CODE_ACCUMULATED_HIGH
    elif score >= MEDIUM_AT and not checked_in:
        target, code = RiskLevel.MEDIUM, CODE_ACCUMULATED_MEDIUM
    else:
        return assessment
    if target <= level:
        return assessment
    return assessment.model_copy(
        update={
            "level": target,
            "rationale_codes": (*assessment.rationale_codes, code),
        }
    )


__all__ = [
    "CODE_ACCUMULATED_HIGH",
    "CODE_ACCUMULATED_MEDIUM",
    "HIGH_AT",
    "MEDIUM_AT",
    "WINDOW_USER_TURNS",
    "ScoredTurn",
    "accumulate",
    "session_score",
]
