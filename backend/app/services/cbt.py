"""Bounded, non-diagnostic thinking-trap suggestions; never relay model prose."""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.llm.base import LLMMessage, LLMProvider
from app.services.nlp.redaction import Redactor
from app.services.safety.base import RiskLevel
from app.services.safety.output_guard import RuleBasedOutputGuard

TRAPS: dict[str, str] = {
    "all-or-nothing": "Seeing a situation as only one of two extremes might be happening.",
    "catastrophising": "Expecting the worst possible outcome might be happening.",
    "mind-reading": "Guessing what others think without checking might be happening.",
    "fortune-telling": "Treating a prediction as certain might be happening.",
    "emotional reasoning": "Treating a feeling as proof might be happening.",
    "should statements": "Holding yourself to a rigid rule might be happening.",
    "labelling": "Reducing a person to one label might be happening.",
    "personalisation": "Taking responsibility for things outside your control might be happening.",
    "filtering": "Noticing only one part of the picture might be happening.",
    "overgeneralisation": "Drawing a broad conclusion from one event might be happening.",
}
QUESTION = "What might you say to a friend in this situation?"
PATTERNS = (
    (r"\b(everyone|they (all|must|will) think)\b", "mind-reading"),
    (r"\b(fail|will|never|going to)\b", "fortune-telling"),
    (r"\b(worst|disaster|ruined)\b", "catastrophising"),
    (r"\b(always|never|everything|nothing|all)\b", "all-or-nothing"),
    (r"\b(should|must|ought)\b", "should statements"),
    (r"\b(stupid|loser|failure)\b", "labelling"),
    (r"\b(feel|feeling)\b", "emotional reasoning"),
    (r"\b(my fault|because of me)\b", "personalisation"),
    (r"\b(only|ignore the good)\b", "filtering"),
    (r"\b(every time|no one ever)\b", "overgeneralisation"),
)


def fallback(text: str) -> list[str]:
    return [name for pattern, name in PATTERNS if re.search(pattern, text, re.I)][:3]


def parse_candidates(text: str) -> list[str]:
    """Only accept a JSON list of exact closed-set IDs; ignore all model prose."""
    try:
        raw: Any = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    selected: list[str] = []
    for item in raw:
        if isinstance(item, str) and item in TRAPS and item not in selected:
            selected.append(item)
    return selected[:3]


async def suggest(
    text: str, provider: LLMProvider | None, *, redact_names: bool = False
) -> tuple[list[str], str]:
    """Called only after input safety screening. Fail locally on any provider/guard failure."""
    if provider is not None:
        try:
            redacted = Redactor(redact_names=redact_names).redact(text).text
            reply = await provider.complete(
                [LLMMessage(role="user", content=redacted)],
                system="Return ONLY a JSON array of up to 3 IDs from: "
                + ", ".join(TRAPS)
                + ". No other text, no diagnoses, no advice.",
                max_tokens=120,
                temperature=0,
            )
            # A canned reply from the provider chain is not structured task output.
            verdict = await RuleBasedOutputGuard().check(reply.text, risk=RiskLevel.NONE)
            if verdict.passed and reply.provider != "canned":
                items = parse_candidates(reply.text)
                if items:
                    return items, "llm"
        except Exception:
            # No text or exception detail is logged. A safe local fallback wins.
            pass
    return fallback(text), "fallback"
