"""Crisis resources: the helpline list behind "Need help now?".

Public by design. Someone in trouble has not signed in and must never be asked
to: an authentication wall in front of a helpline list is a safety bug, not a
security feature. The data is static content (``app/content/helplines.json``)
with a ``last_verified`` date a person has checked (AGENTS.md safety rule 6).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import HelplinesDep
from app.content.crisis import HelplineContent

router = APIRouter(prefix="/crisis", tags=["crisis"])


@router.get("/resources")
async def crisis_resources(content: HelplinesDep) -> HelplineContent:
    """Every helpline, in the order the client should show them.

    No authentication, no consent gate, no account required.
    """
    return content
