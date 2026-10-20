"""Drift monitoring endpoints (Day 48).

Privacy-preserving: the monitor stores only aggregate counts — never text,
never embeddings, never identifiers.  Endpoints expose the aggregated
windows, alerts and the weekly report.
"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.core.metrics import get_metrics
from app.services.drift.monitor import DriftMonitor

router = APIRouter(prefix="/drift", tags=["drift"])

# Single process-wide monitor (tests reset it)
_monitor: DriftMonitor | None = None


def get_monitor(request: Request) -> DriftMonitor:
    global _monitor
    if _monitor is None:
        # try to reuse app state if available
        m = getattr(request.app.state, "drift_monitor", None)
        if m is not None:
            _monitor = m
        else:
            _monitor = DriftMonitor()
            request.app.state.drift_monitor = _monitor
    return _monitor


def reset_monitor() -> DriftMonitor:
    global _monitor
    _monitor = DriftMonitor()
    return _monitor


class RecordInput(BaseModel):
    window: str
    text_length: int
    language: str = "en"
    confidence: float | None = None
    safety_level: str | None = None
    abstained: bool = False
    guard_blocked: bool = False
    embedding: list[float] | None = None


@router.post("/record")
def record_drift(body: RecordInput, request: Request, user: CurrentUser) -> dict[str, Any]:
    del user
    monitor = get_monitor(request)
    monitor.record(
        body.window,
        text_length=body.text_length,
        language=body.language,
        confidence=body.confidence,
        safety_level=body.safety_level,
        abstained=body.abstained,
        guard_blocked=body.guard_blocked,
        embedding=body.embedding,
    )
    with contextlib.suppress(Exception):
        get_metrics().record_drift_check(body.window)
    return {"ok": True, "window": body.window}


@router.get("/alerts")
def drift_alerts(
    window: str, request: Request, user: CurrentUser, baseline: str | None = None
) -> dict[str, Any]:
    del user
    monitor = get_monitor(request)
    alerts = monitor.check_drift(window, baseline_window=baseline)
    try:
        m = get_metrics()
        for a in alerts:
            m.record_drift_alert(a.metric, a.severity)
    except Exception:
        pass
    return {
        "window": window,
        "baseline": baseline or monitor.baseline_window,
        "alerts": [
            {
                "metric": a.metric,
                "psi": a.psi,
                "ks": a.ks,
                "severity": a.severity,
                "message": a.message,
            }
            for a in alerts
        ],
        "count": len(alerts),
    }


@router.get("/report")
def drift_report(
    window: str, request: Request, user: CurrentUser, baseline: str | None = None
) -> dict[str, Any]:
    del user
    monitor = get_monitor(request)
    report = monitor.weekly_report(window, baseline_window=baseline)
    return report


@router.get("/snapshot/{window}")
def drift_snapshot(window: str, request: Request, user: CurrentUser) -> dict[str, Any]:
    del user
    monitor = get_monitor(request)
    snap = monitor.snapshots.get(window)
    if snap is None:
        return {"window": window, "found": False}
    return {"window": window, "found": True, "snapshot": snap.to_dict()}
