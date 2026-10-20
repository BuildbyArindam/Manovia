"""Feature flag store with safe fallbacks (Day 48).

Kill switches that can be turned off without a deploy.  Each flag has a
tested safe fallback that runs when the flag is off.

Flags are stored in-memory with optional DB persistence later; toggling
requires no restart.  Overrides win over Settings defaults.

Per-model and per-feature flags (task requirement):
- voice, agent, forecasting, bandit, private-mode models, XR
plus per-model: safety_ml, emotion, retrieval
"""

from __future__ import annotations

from threading import RLock
from typing import Any

# All known flags with their default (from Settings) and fallback description.
# A new flag must be added here to be togglable; unknown flags are rejected.
KNOWN_FLAGS: dict[str, dict[str, Any]] = {
    # per-feature
    "voice": {"default": True, "fallback": "voice UI hidden, text chat remains; no audio stored"},
    "agent": {
        "default": True,
        "fallback": "agent endpoint returns 503 with help links; chat still works",
    },
    "forecasting": {
        "default": True,
        "fallback": "Mood Outlook card hidden, core mood check-in remains",
    },
    "bandit": {
        "default": True,
        "fallback": "daily plan uses deterministic rules, no learning; why-this still shown",
    },
    "private_mode_models": {
        "default": True,
        "fallback": "Private Mode replies with templates; rules still assess risk",
    },
    "xr": {"default": True, "fallback": "calm space shows 2D canvas only; no WebXR session"},
    "meditation": {
        "default": True,
        "fallback": "meditation page shows curated content only; no LLM passages",
    },
    "biofeedback": {
        "default": True,
        "fallback": "biofeedback page hidden; breathing exercises remain",
    },
    "sharing": {
        "default": True,
        "fallback": "share routes 404; existing links still viewable until expiry",
    },
    # per-model
    "safety_ml": {
        "default": True,
        "fallback": "rules-only safety; ML backstop disabled, recall floor still enforced",
    },
    "emotion": {
        "default": True,
        "fallback": "emotion hint hidden; keyword fallback still available if enabled",
    },
    "retrieval": {
        "default": True,
        "fallback": "no knowledge cards; replies still sent without references",
    },
}


class FeatureFlagStore:
    """Thread-safe in-memory flag store with audit."""

    def __init__(self) -> None:
        self._flags: dict[str, bool] = {k: v["default"] for k, v in KNOWN_FLAGS.items()}
        self._overrides: dict[str, bool] = {}
        self._history: list[dict[str, Any]] = []
        self._lock = RLock()

    def get(self, name: str) -> bool:
        with self._lock:
            if name not in KNOWN_FLAGS:
                raise KeyError(f"unknown flag {name!r}")
            if name in self._overrides:
                return self._overrides[name]
            return self._flags[name]

    def is_enabled(self, name: str) -> bool:
        return self.get(name)

    def set(self, name: str, enabled: bool, actor: str = "system") -> None:
        with self._lock:
            if name not in KNOWN_FLAGS:
                raise KeyError(f"unknown flag {name!r}")
            prev = self._flags.get(name, KNOWN_FLAGS[name]["default"])
            # we store in overrides so defaults remain visible
            self._overrides[name] = bool(enabled)
            self._flags[name] = bool(enabled)
            self._history.append(
                {
                    "flag": name,
                    "from": prev,
                    "to": bool(enabled),
                    "actor": actor,
                    "fallback": KNOWN_FLAGS[name]["fallback"],
                }
            )

    def reset(self, name: str, actor: str = "system") -> None:
        with self._lock:
            if name not in KNOWN_FLAGS:
                raise KeyError(f"unknown flag {name!r}")
            default = KNOWN_FLAGS[name]["default"]
            prev = self._flags.get(name, default)
            self._overrides.pop(name, None)
            self._flags[name] = default
            self._history.append(
                {
                    "flag": name,
                    "from": prev,
                    "to": default,
                    "actor": actor,
                    "fallback": KNOWN_FLAGS[name]["fallback"],
                }
            )

    def all_flags(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._flags)

    def describe(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                name: {
                    "enabled": self._flags[name],
                    "default": KNOWN_FLAGS[name]["default"],
                    "fallback": KNOWN_FLAGS[name]["fallback"],
                    "overridden": name in self._overrides,
                }
                for name in KNOWN_FLAGS
            }

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)


_instance: FeatureFlagStore | None = None
_lock = RLock()


def get_flag_store() -> FeatureFlagStore:
    global _instance
    with _lock:
        if _instance is None:
            _instance = FeatureFlagStore()
        return _instance


def reset_flag_store() -> FeatureFlagStore:
    global _instance
    with _lock:
        _instance = FeatureFlagStore()
        return _instance
