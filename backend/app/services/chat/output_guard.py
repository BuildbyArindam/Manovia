"""Chat-facing exports for the deterministic safety output guard.

The policy and result types live under ``services/safety`` because the check is
a safety boundary. ``PassthroughOutputGuard`` remains available solely for
focused tests; production orchestration defaults to ``RuleBasedOutputGuard``.
"""

from __future__ import annotations

from app.services.safety.base import RiskLevel
from app.services.safety.output_guard import GuardVerdict, OutputGuard


class PassthroughOutputGuard:
    """Approves everything. A test double only; production does not use it."""

    requires_full_text = False

    async def check(
        self,
        text: str,
        *,
        risk: RiskLevel,
        expected_language: str | None = None,
    ) -> GuardVerdict:
        return GuardVerdict(text=text, passed=True)


__all__ = ["GuardVerdict", "OutputGuard", "PassthroughOutputGuard"]
