"""The transparent action log.

Every tool call the agent proposes — executed, rejected,
quarantined, confirmed, denied — is recorded here and
returned to the person with the reply. Transparency is the
feature: the person can audit exactly what the agent tried
to do on their behalf, including the attempts that were
stopped.

Privacy (AGENTS.md rule 5): entries carry validated,
bounded tool arguments and neutral outcome summaries —
never raw message text, never a fingerprint of the person.
"""

from __future__ import annotations

from datetime import datetime

from app.services.agent.types import ActionLogEntry, LogEvent


class ActionLog:
    """An ordered, bounded record of one turn's agent activity."""

    def __init__(self) -> None:
        self._entries: list[ActionLogEntry] = []

    def record(
        self,
        event: LogEvent,
        *,
        tool: str | None = None,
        arguments: dict[str, object] | None = None,
        outcome: str | None = None,
        reason: str | None = None,
        at: datetime | None = None,
    ) -> None:
        entry = ActionLogEntry(
            seq=len(self._entries) + 1,
            at=at or datetime.now().astimezone(),
            event=event,
            tool=tool,
            arguments=arguments,
            outcome=outcome,
            reason=reason,
        )
        self._entries.append(entry)

    def tool_proposed(self, tool: str, arguments: dict[str, object]) -> None:
        self.record("tool_proposed", tool=tool, arguments=arguments)

    def tool_executed(self, tool: str, outcome: str) -> None:
        self.record("tool_executed", tool=tool, outcome=outcome)

    def tool_rejected(self, tool: str, reason: str) -> None:
        self.record("tool_rejected", tool=tool, reason=reason)

    def confirmation_required(self, tool: str, summary: str) -> None:
        self.record("confirmation_required", tool=tool, outcome=summary)

    def confirmation_allowed(self, tool: str) -> None:
        self.record("confirmation_allowed", tool=tool)

    def confirmation_denied(self, tool: str) -> None:
        self.record("confirmation_denied", tool=tool)

    def injection_quarantined(self, tool: str) -> None:
        self.record(
            "injection_quarantined",
            tool=tool,
            outcome=("Instruction-shaped text found in tool output; withheld and not obeyed."),
        )

    def safety_gate_blocked(self) -> None:
        self.record("safety_gate_blocked", outcome="Agent not invoked.")

    def step_limit_reached(self) -> None:
        self.record("step_limit_reached")

    def token_budget_exhausted(self) -> None:
        self.record("token_budget_exhausted")

    def final_answer(self, outcome: str) -> None:
        self.record("final_answer", outcome=outcome)

    @property
    def entries(self) -> list[ActionLogEntry]:
        return list(self._entries)

    def as_list(self) -> list[ActionLogEntry]:
        return self.entries
