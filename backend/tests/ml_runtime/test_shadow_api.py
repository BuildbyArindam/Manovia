"""API-level shadow contract: identical user-visible responses, on vs off.

Uses the shared fixtures (test DB, rules-only settings) plus a fake
candidate injected into ``app.state`` — the transport, pipeline and
escalation plan are the real ones; only the candidate model is a double,
which is exactly the substitution shadow mode itself makes.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest

from app.core.config import Settings
from app.db.session import Database
from app.main import create_app
from app.services.ml_runtime.shadow import ShadowRunner

ASSESS = "/api/v1/crisis/assess"
_LABELS = ("none", "low", "medium", "high", "imminent")


@dataclass
class _Prediction:
    probabilities: tuple[float, ...]


class _Candidate:
    def predict(self, text: str) -> _Prediction:
        return _Prediction((0.05, 0.05, 0.1, 0.4, 0.4))


@pytest.mark.asyncio
async def test_responses_identical_with_shadow_on_and_off(
    settings: Settings, database: Database
) -> None:
    messages = [
        "i feel a bit tired today",
        "the train was late again",
        "i feel hopeless and cannot sleep",
        "made soup from scratch tonight",
    ]

    async def _collect(with_shadow: bool) -> list[object]:
        app = create_app(settings, database=database)
        if with_shadow:
            app.state.shadow_runner = ShadowRunner(
                _Candidate(), candidate_version="cand-v1", candidate_labels=_LABELS
            )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            bodies: list[object] = []
            for text in messages:
                response = await client.post(ASSESS, json={"text": text, "region": "IN"})
                assert response.status_code == 200
                bodies.append(response.json())
            if with_shadow:
                app.state.shadow_runner.flush()
                assert app.state.shadow_runner.records, "shadow must have observed traffic"
                categories = {r.category for r in app.state.shadow_runner.records}
                assert categories <= {
                    "agree",
                    "candidate_higher",
                    "candidate_lower",
                    "candidate_crisis_only",
                    "production_crisis_only",
                    "candidate_timeout",
                    "candidate_error",
                }
            return bodies

    # Production prediction is None under rules-only settings (ML disabled),
    # so shadow observes only when the classifier runs. Enable the baseline
    # classifier for both arms so the comparison is production-vs-candidate.
    settings.safety_ml_enabled = True
    off = await _collect(False)
    on = await _collect(True)
    assert on == off, "shadow mode must never change the user-visible response"
