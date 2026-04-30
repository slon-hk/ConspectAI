"""Client-side analytics API routes."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.analytics_tracking_service import AnalyticsTrackingService

CLIENT_EVENT_ALLOWLIST = {
    "landing_cta_click",
    "buy_modal_opened",
    "export_md",
    "export_pdf",
    "settings_opened",
}


class TrackIn(BaseModel):
    event: str
    props: dict = {}


def create_analytics_router(
    *,
    token_dependency: Callable,
    decode_token: Callable[[str], int | None],
    analytics_tracking_service: AnalyticsTrackingService,
) -> APIRouter:
    router = APIRouter(tags=["analytics"])

    @router.post("/api/track")
    async def client_track(
        body: TrackIn,
        token: str = Depends(token_dependency),
    ):
        if body.event not in CLIENT_EVENT_ALLOWLIST:
            raise HTTPException(400, f"Unknown event: {body.event}")

        uid = decode_token(token) if token else None
        safe_props = {}
        for key, value in (body.props or {}).items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe_props[str(key)[:40]] = value if not isinstance(value, str) else value[:200]
        analytics_tracking_service.track(body.event, uid, **safe_props)
        return {"ok": True}

    return router
