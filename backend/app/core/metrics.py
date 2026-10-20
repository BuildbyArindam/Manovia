"""Prometheus metrics that respect privacy (Day 27).

One rule shapes everything here: **labels carry categories, never content.**
The exported series answer the questions the runbook asks — request rate,
latency, error rate, safety events by level, LLM fallback rate, output-guard
block rate — and not one label value is derived from anything a person typed
or from any identifier. Route templates (``/api/v1/chat/{session_id}``) are
the only path form ever exported; unmatched routes export the fixed label
``:unmatched`` so a 404 flood cannot inflate label cardinality.

Everything lives in one per-process ``CollectorRegistry`` behind the
``Metrics`` singleton. Counters are process-local: with several Gunicorn
workers, Prometheus aggregates across them (that is what rate/avg queries do);
nothing here needs a shared store. The registry is injected, so tests build a
fresh ``Metrics`` per test and assert exact label values in isolation.
"""

from __future__ import annotations

from typing import Final

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from starlette.types import Scope

#: Fixed label for requests that matched no route (404s, method mismatches).
UNMATCHED_PATH: Final[str] = ":unmatched"

#: Latency buckets, seconds. The upper half covers the LLM budget (15 s
#: provider timeout) and the app's own 30 s request deadline; SSE streams are
#: exempt from that deadline and can exceed every bucket (observed as +Inf).
LATENCY_BUCKETS: Final[tuple[float, ...]] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    20.0,
    30.0,
)

#: Outcomes the output guard can produce (closed set — no free text).
GUARD_OUTCOMES: Final[tuple[str, ...]] = (
    "passed",
    "passed_after_retry",
    "blocked",
    "error",
)

#: Why the LLM chain fell back from the leading provider (closed set).
LLM_FALLBACK_REASONS: Final[tuple[str, ...]] = (
    "provider_failed",
    "provider_crashed",
    "not_configured",
)


def _safety_level_label(risk_level: int) -> str:
    """Map a stored risk level to its fixed, low-cardinality label."""
    from app.models.enums import RiskLevel

    try:
        return RiskLevel(int(risk_level)).name.lower()
    except ValueError:
        return "unknown"


class Metrics:
    """The app's metric collectors, bound to one registry."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)

        namespace = "manovia"

        # --- HTTP service health ------------------------------------------
        self.http_requests_total = Counter(
            "http_requests_total",
            "HTTP requests handled, by method, route template and status.",
            labelnames=("method", "path", "status"),
            namespace=namespace,
            registry=self.registry,
        )
        self.http_request_seconds = Histogram(
            "http_request_seconds",
            "End-to-end request duration in seconds (route-template label).",
            labelnames=("method", "path"),
            buckets=LATENCY_BUCKETS,
            namespace=namespace,
            registry=self.registry,
        )
        self.http_requests_in_progress = Gauge(
            "http_requests_in_progress",
            "Requests currently being served, by method.",
            labelnames=("method",),
            namespace=namespace,
            registry=self.registry,
        )

        # --- Safety (Day 27 requirement: safety events by level) ----------
        self.safety_events_total = Counter(
            "safety_events_total",
            "Safety events recorded, by risk level and detecting source. "
            "Levels and sources are closed enums; no content is exported.",
            labelnames=("level", "source"),
            namespace=namespace,
            registry=self.registry,
        )

        # --- LLM resilience (Day 27 requirement: fallback rate) -----------
        self.llm_requests_total = Counter(
            "llm_requests_total",
            "Chain completions by the provider that served them.",
            labelnames=("provider", "position"),
            namespace=namespace,
            registry=self.registry,
        )
        self.llm_fallbacks_total = Counter(
            "llm_fallbacks_total",
            "Requests the leading provider could not serve, by reason. "
            "A rate near zero is healthy; a rising rate means the runbook's "
            "LLM-outage page applies.",
            labelnames=("reason",),
            namespace=namespace,
            registry=self.registry,
        )

        # --- Output guard (Day 27 requirement: block rate) ----------------
        self.output_guard_total = Counter(
            "output_guard_total",
            "Output-guard verdicts for generated replies, by outcome.",
            labelnames=("outcome",),
            namespace=namespace,
            registry=self.registry,
        )

        # --- Drift monitoring (Day 48): alerts fired, never raw inputs ----
        self.drift_alerts_total = Counter(
            "drift_alerts_total",
            "Drift alerts fired, by metric and severity. No text, no ids.",
            labelnames=("metric", "severity"),
            namespace=namespace,
            registry=self.registry,
        )
        self.drift_checks_total = Counter(
            "drift_checks_total",
            "Drift checks performed, by window.",
            labelnames=("window",),
            namespace=namespace,
            registry=self.registry,
        )

        # --- Kill-switch fallback invocations (Day 48) --------------------
        self.feature_fallback_total = Counter(
            "feature_fallback_total",
            "Times a disabled feature served its safe fallback.",
            labelnames=("feature",),
            namespace=namespace,
            registry=self.registry,
        )

    # --- recording helpers (used by services; keep call sites one-liners) --

    def observe_request(self, method: str, path: str, status: int, duration_seconds: float) -> None:
        """Record one completed request against its route template."""
        self.http_requests_total.labels(method=method, path=path, status=str(status)).inc()
        self.http_request_seconds.labels(method=method, path=path).observe(duration_seconds)

    def start_request(self, method: str) -> None:
        self.http_requests_in_progress.labels(method=method).inc()

    def finish_request(self, method: str) -> None:
        self.http_requests_in_progress.labels(method=method).dec()

    def record_safety_event(self, risk_level: int, source: str) -> None:
        """Count one safety event. ``source`` is a SafetyEventSource value."""
        self.safety_events_total.labels(level=_safety_level_label(risk_level), source=source).inc()

    def record_llm_success(self, provider: str, position: int) -> None:
        """One chain completion. ``position > 0`` means a fallback served it."""
        self.llm_requests_total.labels(provider=provider, position=str(position)).inc()

    def record_llm_fallback(self, reason: str) -> None:
        """The chain moved past a provider. ``reason`` is from the closed set."""
        self.llm_fallbacks_total.labels(reason=reason).inc()

    def record_guard_outcome(self, outcome: str) -> None:
        """One output-guard verdict; ``outcome`` is from the closed set."""
        self.output_guard_total.labels(outcome=outcome).inc()

    def record_drift_alert(self, metric: str, severity: str) -> None:
        self.drift_alerts_total.labels(metric=metric, severity=severity).inc()

    def record_drift_check(self, window: str) -> None:
        self.drift_checks_total.labels(window=window).inc()

    def record_feature_fallback(self, feature: str) -> None:
        self.feature_fallback_total.labels(feature=feature).inc()


_instance: Metrics | None = None


def get_metrics() -> Metrics:
    """The process-wide metrics instance (created on first use)."""
    global _instance
    if _instance is None:
        _instance = Metrics()
    return _instance


def reset_metrics() -> Metrics:
    """Drop the singleton and build a fresh one (tests only)."""
    global _instance
    _instance = Metrics()
    return _instance


def _build_route_template_map(app: object) -> dict[int, str]:
    """Map original route objects to their full (prefix-included) template.

    FastAPI ≥0.13 keeps included routers lazily mounted (``_IncludedRouter``):
    the route Starlette writes to ``scope[\"route\"]`` carries its *own* path
    (``/health``), not the mounted one (``/api/v1/health``). To label metrics
    with the path a person's browser actually called, the middleware needs the
    prefix too. This walks the app's route tree once, per app, and resolves
    each original route object to ``include-context prefix + route path``.

    Defensive by design: every access goes through ``getattr``, and anything
    the walker cannot understand is simply absent from the map (the caller
    falls back to ``scope[\"route\"].path`` and then to the fixed unmatched
    label). The private types it reads are pinned by ``fastapi==0.143.0``.
    """
    mapping: dict[int, str] = {}

    def walk(router: object, prefix: str) -> None:
        routes = getattr(router, "routes", None)
        if not isinstance(routes, (list, tuple)):
            return
        for route in routes:
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                # A lazily-included FastAPI router: its include context holds
                # the prefix it was mounted under.
                context = getattr(route, "include_context", None)
                inner_prefix = str(getattr(context, "prefix", "") or "")
                walk(original_router, prefix + inner_prefix)
                continue
            path = getattr(route, "path", None)
            if isinstance(path, str) and path:
                mapping[id(route)] = prefix + path

    walk(getattr(app, "router", None), "")
    return mapping


def path_template_for_scope(scope: Scope) -> str:
    """The full route template for a request scope, or a fixed unmatched label.

    Resolution order: the per-app template map (full path with mount prefix,
    e.g. ``/api/v1/chat/{session_id}``), then ``scope[\"route\"].path`` for
    plain Starlette routers, then the fixed ``:unmatched`` label. Exporting
    the raw path would make one label value per conversation — that is an
    identifier leak and a cardinality bomb, so it never happens.
    """
    route: object | None = scope.get("route")
    if route is None:
        return UNMATCHED_PATH
    app = scope.get("app")
    if app is not None:
        state = getattr(app, "state", None)
        mapping = getattr(state, "metrics_route_templates", None) if state is not None else None
        if mapping is None and state is not None:
            # First request: build the map once and cache it on app.state.
            mapping = _build_route_template_map(app)
            state.metrics_route_templates = mapping
        if mapping:
            original = getattr(route, "original_route", route)
            template = mapping.get(id(original))
            if isinstance(template, str) and template:
                return template
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return UNMATCHED_PATH
