"""/metrics — the Prometheus scrape endpoint (Day 27).

Mounted at the server root, outside ``/api/v1``: scraper conventions win here,
and the versioned API surface stays purely product. The body is the standard
Prometheus text exposition of the app's registry.

Privacy posture (AGENTS.md rule 5): every label is a closed-set category or a
route template. No user text, no identifiers, no DSNs. When
``METRICS_ENABLED=false`` the route is not registered at all (the same pattern
as the dev-only router), so a scrape sees the plain 404 an unknown path gets.
"""

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.metrics import get_metrics

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Expose the app's counters and histograms in Prometheus text format."""
    return Response(
        content=generate_latest(get_metrics().registry),
        media_type=CONTENT_TYPE_LATEST,
    )
