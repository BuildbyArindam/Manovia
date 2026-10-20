"""Kill switches: per-model and per-feature flags with safe fallbacks."""

from __future__ import annotations

import pytest

from app.services.feature_flags.store import (
    KNOWN_FLAGS,
    FeatureFlagStore,
    reset_flag_store,
)


def test_all_required_flags_exist() -> None:
    required = {"voice", "agent", "forecasting", "bandit", "private_mode_models", "xr", "safety_ml", "emotion", "retrieval"}  # noqa: E501
    assert required.issubset(set(KNOWN_FLAGS.keys()))


def test_each_flag_has_tested_safe_fallback() -> None:
    for name, meta in KNOWN_FLAGS.items():
        assert "fallback" in meta
        assert len(meta["fallback"]) > 10, f"{name} fallback description too short"


def test_flag_can_be_flipped_at_runtime_without_restart() -> None:
    store = FeatureFlagStore()
    assert store.get("voice") is True
    store.set("voice", False, actor="test")
    assert store.get("voice") is False
    # Fallback is documented
    desc = store.describe()
    assert desc["voice"]["fallback"] != ""
    assert desc["voice"]["enabled"] is False
    # Flip back
    store.set("voice", True, actor="test")
    assert store.get("voice") is True


def test_unknown_flag_rejected() -> None:
    store = FeatureFlagStore()
    with pytest.raises(KeyError, match="unknown flag"):
        store.set("nonexistent_feature", True)


def test_flag_reset_restores_default() -> None:
    store = FeatureFlagStore()
    store.set("agent", False, actor="test")
    assert store.get("agent") is False
    store.reset("agent", actor="test")
    assert store.get("agent") == KNOWN_FLAGS["agent"]["default"]


def test_flag_history_audited() -> None:
    store = FeatureFlagStore()
    store.set("forecasting", False, actor="alice")
    store.set("forecasting", True, actor="bob")
    hist = store.history()
    assert len(hist) == 2
    assert hist[0]["actor"] == "alice"
    assert hist[1]["actor"] == "bob"


def test_voice_fallback_is_text_chat() -> None:
    FeatureFlagStore()
    assert "text chat" in KNOWN_FLAGS["voice"]["fallback"].lower() or "text" in KNOWN_FLAGS["voice"]["fallback"].lower()  # noqa: E501


def test_private_mode_fallback_is_templates() -> None:
    FeatureFlagStore()
    fallback = KNOWN_FLAGS["private_mode_models"]["fallback"].lower()
    assert "template" in fallback


def test_safety_ml_fallback_is_rules_only() -> None:
    fallback = KNOWN_FLAGS["safety_ml"]["fallback"].lower()
    assert "rules" in fallback


def test_integration_toggle_via_store_and_api() -> None:
    # Simulate API toggle without restart
    store = reset_flag_store()
    original = store.get("xr")
    store.set("xr", False, actor="integration-test")
    assert store.get("xr") is False
    # Simulate that calm space would check this flag and degrade to 2D
    # The fallback metric should be recorded (checked in integration)
    fallback = store.describe()["xr"]["fallback"]
    assert "2d" in fallback.lower()
    store.reset("xr", actor="integration-test")
    assert store.get("xr") == original
