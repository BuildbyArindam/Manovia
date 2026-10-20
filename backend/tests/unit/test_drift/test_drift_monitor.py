"""Drift monitor: privacy-preserving aggregates, PSI/KS alerts."""

from __future__ import annotations

from app.services.drift.monitor import DriftMonitor


def test_drift_stores_only_aggregates_never_text() -> None:
    m = DriftMonitor()
    m.record(
        "2026-W40",
        text_length=42,
        language="en",
        confidence=0.9,
        safety_level="none",
        embedding=[0.1, 0.2, 0.3, 0.4],
    )
    snap = m.snapshots["2026-W40"]
    # The snapshot must not contain raw text
    assert "text" not in snap.to_dict()
    assert snap.total_inputs == 1
    assert snap.embedding_count == 1
    # embedding_count stores centroid, not raw vectors
    assert len(snap.embedding_centroid) == 4
    assert snap.embedding_count == 1


def test_drift_alert_fires_on_shifted_language_and_length() -> None:
    m = DriftMonitor()
    # baseline: mostly en, short
    for _ in range(100):
        m.record(
            "baseline",
            text_length=30,
            language="en",
            confidence=0.9,
            safety_level="none",
            embedding=[0.0, 0.0, 0.0, 0.0],
        )
    # current: mostly hi, long, lower confidence
    for _ in range(100):
        m.record(
            "current",
            text_length=300,
            language="hi",
            confidence=0.4,
            safety_level="medium",
            abstained=True,
            embedding=[0.5, 0.5, 0.5, 0.5],
        )
    alerts = m.check_drift("current", baseline_window="baseline")
    assert len(alerts) >= 2
    metrics = {a.metric for a in alerts}
    assert "language_mix" in metrics or "input_length" in metrics
    assert any(a.severity in ("warning", "critical") for a in alerts)


def test_drift_weekly_report_contains_required_fields() -> None:
    m = DriftMonitor()
    for _ in range(50):
        m.record("2026-W40", text_length=40, language="en", confidence=0.8, safety_level="none")
    for _ in range(50):
        m.record("2026-W42", text_length=40, language="en", confidence=0.8, safety_level="none")
    report = m.weekly_report("2026-W42", baseline_window="2026-W40")
    assert "baseline_window" in report
    assert "current_window" in report
    assert "baseline" in report
    assert "current" in report
    assert "alerts" in report
    assert report["current"]["total_inputs"] == 50


def test_privacy_no_identifiers_in_snapshot() -> None:
    m = DriftMonitor()
    m.record("w1", text_length=100, language="en", confidence=0.7)
    d = m.snapshots["w1"].to_dict()
    # Ensure no identifier-like keys leaked
    forbidden = {"user_id", "email", "text", "embedding_raw", "identifier"}
    assert not any(k in d for k in forbidden)


def test_embedding_centroid_shift_computed_privacy_preserving() -> None:
    m = DriftMonitor()
    for _ in range(10):
        m.record("base", text_length=50, language="en", embedding=[0.0, 0.0, 0.0, 0.0])
    for _ in range(10):
        m.record("cur", text_length=50, language="en", embedding=[1.0, 1.0, 1.0, 1.0])
    alerts = m.check_drift("cur", baseline_window="base")
    # centroid shift should be sqrt(4) = 2.0 -> critical
    assert any(a.metric == "embedding_centroid_shift" and a.severity == "critical" for a in alerts)
