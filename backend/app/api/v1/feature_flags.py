"""Runtime kill switches: list flags and flip them without a deploy (Day 48).

Each flag has a tested safe fallback — turning it off degrades the feature
rather than crashing the app. Overrides are in-memory and survive until the
process restarts; they are visible in ``GET /admin/feature-flags`` and in the
audit history.

Access: authenticated users can READ flags; only a privileged actor could
WRITE in production — in this sandbox we allow any authenticated user to
flip for testing, and we log the actor id.
"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.core.metrics import get_metrics
from app.services.feature_flags.store import get_flag_store

router = APIRouter(prefix="/feature-flags", tags=["feature-flags"])


class FlagToggle(BaseModel):
    enabled: bool


@router.get("")
def list_flags(user: CurrentUser, request: Request) -> dict[str, Any]:
    del request
    store = get_flag_store()
    return {"flags": store.describe(), "history": store.history()[-20:]}


@router.post("/{flag_name}")
def set_flag(
    flag_name: str, body: FlagToggle, user: CurrentUser, request: Request
) -> dict[str, Any]:
    del request
    store = get_flag_store()
    # actor is user id for audit
    actor = str(getattr(user, "id", "unknown"))
    try:
        store.set(flag_name, body.enabled, actor=actor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not body.enabled:
        with contextlib.suppress(Exception):
            get_metrics().record_feature_fallback(flag_name)
    return {
        "flag": flag_name,
        "enabled": body.enabled,
        "fallback": store.describe()[flag_name]["fallback"],
    }


@router.post("/{flag_name}/reset")
def reset_flag(
    flag_name: str, user: CurrentUser, request: Request
) -> dict[str, Any]:
    del request
    store = get_flag_store()
    actor = str(getattr(user, "id", "unknown"))
    try:
        store.reset(flag_name, actor=actor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"flag": flag_name, "enabled": store.get(flag_name), "reset_to_default": True}
