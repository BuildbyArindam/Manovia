"""Token and cost guard: nothing unbounded reaches a metered API.

Two different problems, two different mechanisms:

**Max input length.** One message must not be able to cost more than the whole
conversation budget. Over-long input is *truncated on a word boundary*, never
rejected — somebody who writes a lot is often somebody who needs the reply, and
"your message was too long" is a cruel response to a long cry for help.

**Conversation window.** A chat grows without limit, and the whole history is
sent on every turn, so the cost of turn 40 is 40 times the cost of turn 1. The
window keeps the most recent turns that fit the token budget, oldest dropped
first, and it **never drops the newest user message** — that is the one turn
the reply is about.

Truncation cuts on a word boundary and appends :data:`TRUNCATION_MARKER`, so:

* a redaction placeholder is never cut in half (``[PHO`` would no longer be a
  placeholder, and the guard runs after redaction in
  :class:`~app.services.llm.chain.LLMChain`);
* the model can see that text was removed instead of reading a sentence that
  stops mid-thought as if the user stopped mid-thought.

The token estimate is deliberately crude: 4 characters ≈ 1 token, counted
deterministically. Providers report the true usage and
:class:`~app.services.llm.base.LLMResult.usage` carries it; this number only has
to be *conservative enough* to keep a request inside its budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.services.llm.base import LLMMessage

#: Characters per token. Over-estimates for English, under-estimates for
#: Devanagari/Bengali (multi-byte UTF-8), which is why the budget is a setting
#: rather than a law of nature.
CHARS_PER_TOKEN: Final[int] = 4

#: Appended to text that had to be cut, so the model knows and so a reader does
#: not read a truncated sentence as the user's own ending.
TRUNCATION_MARKER: Final[str] = " […]"


@dataclass(frozen=True)
class GuardReport:
    """What the guard did. Metadata only — never the text it acted on."""

    input_chars: int = 0
    input_tokens: int = 0
    turns_in: int = 0
    turns_out: int = 0
    dropped_turns: int = 0
    truncated_turns: int = 0
    within_budget: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "input_chars": self.input_chars,
            "input_tokens": self.input_tokens,
            "turns_in": self.turns_in,
            "turns_out": self.turns_out,
            "dropped_turns": self.dropped_turns,
            "truncated_turns": self.truncated_turns,
            "within_budget": self.within_budget,
        }


@dataclass(frozen=True)
class GuardedRequest:
    """Messages and system prompt, after the guard has had its way with them."""

    messages: tuple[LLMMessage, ...]
    system: str | None
    report: GuardReport


class TokenGuard:
    """Truncate long input and window the conversation to a token budget."""

    def __init__(
        self,
        *,
        max_input_chars: int = 8000,
        window_turns: int = 12,
        window_tokens: int = 3000,
        chars_per_token: int = CHARS_PER_TOKEN,
    ) -> None:
        if max_input_chars < 1:
            raise ValueError("max_input_chars must be at least 1")
        if window_turns < 1:
            raise ValueError("window_turns must be at least 1")
        if window_tokens < 1:
            raise ValueError("window_tokens must be at least 1")
        if chars_per_token < 1:
            raise ValueError("chars_per_token must be at least 1")
        self._max_input_chars = max_input_chars
        self._window_turns = window_turns
        self._window_tokens = window_tokens
        self._chars_per_token = chars_per_token

    @property
    def max_input_chars(self) -> int:
        return self._max_input_chars

    @property
    def window_turns(self) -> int:
        return self._window_turns

    @property
    def window_tokens(self) -> int:
        return self._window_tokens

    def estimate_tokens(self, text: str) -> int:
        """Conservative token estimate; never returns 0 for non-empty text."""
        if not text:
            return 0
        return max(1, (len(text) + self._chars_per_token - 1) // self._chars_per_token)

    def truncate(self, text: str, limit: int | None = None) -> tuple[str, bool]:
        """Cut ``text`` to ``limit`` characters at a word boundary.

        Returns ``(text, was_truncated)``. The marker is included in the budget,
        so the result is never longer than ``limit``.
        """
        cap = self._max_input_chars if limit is None else limit
        if cap < 1:
            raise ValueError("limit must be at least 1")
        if len(text) <= cap:
            return text, False
        # Leave room for the marker; if the cap is too small to hold it, cut
        # the marker rather than overrunning the budget.
        room = cap - len(TRUNCATION_MARKER)
        if room <= 0:
            return text[:cap], True
        window = text[:room]
        # Cut at the last whitespace so no word (and no placeholder) is split.
        boundary = max(window.rfind(" "), window.rfind("\n"), window.rfind("\t"))
        if boundary > room // 2:
            window = window[:boundary]
        return window.rstrip() + TRUNCATION_MARKER, True

    def guard(
        self,
        messages: list[LLMMessage] | tuple[LLMMessage, ...],
        system: str | None = None,
    ) -> GuardedRequest:
        """Truncate every turn, then window the conversation to the budget."""
        truncated_turns = 0
        prepared: list[LLMMessage] = []
        for message in messages:
            text, was_truncated = self.truncate(message.content)
            truncated_turns += int(was_truncated)
            if text != message.content:
                prepared.append(LLMMessage(role=message.role, content=text))
            else:
                prepared.append(message)

        # Newest first, take turns until the budget is spent, then restore order.
        system_tokens = self.estimate_tokens(system or "")
        budget = max(1, self._window_tokens - system_tokens)
        kept: list[LLMMessage] = []
        used = 0
        for message in reversed(prepared):
            if len(kept) >= self._window_turns:
                break
            cost = self.estimate_tokens(message.content)
            # The newest turn always survives, however large: dropping it would
            # leave the model answering a question it cannot see.
            if kept and used + cost > budget:
                break
            kept.append(message)
            used += cost
        kept.reverse()

        report = GuardReport(
            input_chars=sum(len(m.content) for m in kept) + len(system or ""),
            input_tokens=used + system_tokens,
            turns_in=len(prepared),
            turns_out=len(kept),
            dropped_turns=len(prepared) - len(kept),
            truncated_turns=truncated_turns,
            within_budget=used <= budget,
        )
        return GuardedRequest(messages=tuple(kept), system=system, report=report)

    def output_budget(
        self, *, input_tokens: int, max_tokens: int, ceiling_tokens: int = 8192
    ) -> int:
        """How many output tokens are affordable for this request.

        A hard ceiling per request, independent of what the caller asked for:
        ``max_tokens`` is the *desired* length, not a licence to spend.
        """
        affordable = ceiling_tokens - max(0, input_tokens)
        return max(1, min(max_tokens, affordable))

    def describe(self) -> dict[str, object]:
        return {
            "max_input_chars": self._max_input_chars,
            "window_turns": self._window_turns,
            "window_tokens": self._window_tokens,
            "chars_per_token": self._chars_per_token,
        }
