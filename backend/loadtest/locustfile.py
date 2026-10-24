"""Day 26 load test: mood check-ins plus chat against the real app (Fake LLM).

Run headless (see ``backend/loadtest/README.md`` for the exact commands)::

    locust -f loadtest/locustfile.py --headless -u 100 -r 20 -t 2m \\
        --host http://127.0.0.1:8000 --csv loadtest/out/run

Each simulated person signs in as a guest once, grants consent once, then mixes
mood check-ins (the non-LLM path the p95 < 500 ms target applies to) with chat
messages (Fake LLM, so latency measures Manovia, not a vendor). Nothing here
sends real text: the messages are fixed, neutral sentences.
"""

from __future__ import annotations

import random
import uuid

from locust import HttpUser, between, task

VERSION = "2026-10-01"
NEUTRAL_MESSAGES = (
    "I had a long day and I'd like to wind down.",
    "Work was busy but I managed to take a walk.",
    "I'm a bit tired and want to talk it through.",
)


class Person(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        response = self.client.post("/api/v1/auth/guest", name="auth/guest")
        response.raise_for_status()
        token = response.json()["access_token"]
        self.client.headers["Authorization"] = f"Bearer {token}"
        self.client.post(
            "/api/v1/consent",
            name="consent",
            json={
                "grants": [
                    {"kind": kind, "version": VERSION, "granted": True}
                    for kind in ("terms", "privacy", "ai_disclosure")
                ]
            },
        ).raise_for_status()
        opened = self.client.post(
            "/api/v1/chat/sessions", name="chat/sessions", json={"save_history": False}
        )
        opened.raise_for_status()
        self.session_id = opened.json()["id"]

    @task(5)
    def mood_check_in(self) -> None:
        self.client.post(
            "/api/v1/mood",
            name="mood [non-LLM]",
            json={
                "valence": random.randint(1, 5),
                "energy": random.randint(1, 5),
                "emotions": [],
                "factors": ["sleep"],
                "client_id": str(uuid.uuid4()),
            },
        )

    @task(2)
    def read_streak(self) -> None:
        self.client.get("/api/v1/mood/streak", name="mood/streak [non-LLM]")

    @task(2)
    def list_mood(self) -> None:
        self.client.get("/api/v1/mood", name="mood list [non-LLM]")

    @task(1)
    def chat(self) -> None:
        self.client.post(
            f"/api/v1/chat/sessions/{self.session_id}/messages",
            name="chat message [Fake LLM]",
            json={"message": random.choice(NEUTRAL_MESSAGES)},
        )
