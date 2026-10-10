"""Output guard stub — Day 14 fills it.

Today it passes the LLM text through unchanged. The interface is a function
that takes the raw completion and returns a (possibly modified) completion plus
a flag saying whether it was blocked.

Day 14 will add checks for diagnosis claims, medication advice, method language,
claimed humanity, etc., reusing the safe-messaging vocabulary from Day 8.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuardResult:
    """What the output guard decided."""

    text: str
    blocked: bool = False
    reason: str | None = None
    replacement: str | None = None


def guard_output(text: str) -> GuardResult:
    """Check ``text`` before it reaches the user. Stub: always passes."""
    return GuardResult(text=text, blocked=False)


async def aguard_output(text: str) -> GuardResult:
    """Async wrapper for symmetry with the LLM chain."""
    return guard_output(text)


__all__ = ["GuardResult", "aguard_output", "guard_output"]
