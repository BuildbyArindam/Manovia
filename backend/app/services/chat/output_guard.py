"""Step 8 — the output guard. A pass-through today; Day 14 fills it.

AGENTS.md safety rule 4 says every LLM output passes an output-safety check
before reaching the user. The *seam* exists now so that the rule is structural:
the orchestrator cannot hand a model reply to a client without passing it
through :meth:`OutputGuard.check`.

**Streaming and the guard.** A guard that needs the whole reply (almost any real
one does) cannot run while tokens are already on the wire. So a guard declares
:attr:`OutputGuard.requires_full_text`: when true, the orchestrator buffers the
model's stream, checks the whole text, and only then releases tokens — the
person sees a short wait instead of an unchecked sentence. The pass-through stub
says ``False`` and streams live. Day 14 flips that flag; nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.services.safety.base import RiskLevel


@dataclass(frozen=True)
class GuardVerdict:
    """The guard's decision about one model reply."""

    #: The text to show. Equal to the input when the guard passed it; a safe
    #: replacement otherwise.
    text: str
    #: False when ``text`` is a replacement for what the model wrote.
    passed: bool = True
    #: A stable code naming the rule that fired — never a fragment of the text.
    reason: str | None = None


@runtime_checkable
class OutputGuard(Protocol):
    """Checks a model reply before it is shown or stored."""

    #: True when the guard cannot judge a partial reply, so streaming must be
    #: buffered until the full text has been checked.
    requires_full_text: bool

    async def check(self, text: str, *, risk: RiskLevel) -> GuardVerdict:
        """Pass ``text`` through, or return a replacement and ``passed=False``."""
        ...


class PassthroughOutputGuard:
    """Approves everything. A placeholder, deliberately named as one."""

    requires_full_text = False

    async def check(self, text: str, *, risk: RiskLevel) -> GuardVerdict:
        return GuardVerdict(text=text, passed=True)
