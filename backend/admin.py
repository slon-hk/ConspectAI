"""
Admin API — routes mounted at /api/admin/*.
Access controlled by the require_admin dependency.

To grant admin privileges to the first user:
    docker compose exec db psql -U orion -d orion \
        -c "UPDATE users SET is_admin=true WHERE email='you@example.com';"
"""

from fastapi import APIRouter, Depends, HTTPException, Form
from pydantic import BaseModel

import db
import auth

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── Dependency ────────────────────────────────────────────────────────────────
async def require_admin(token: str = Depends(auth.oauth2)) -> dict:
    if not token:
        raise HTTPException(401, "Not authenticated")
    uid = auth.decode_token(token)
    if not uid:
        raise HTTPException(401, "Invalid token")
    user = await db.get_user_by_id(uid)
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Admin access required")
    return user


# ── Schemas ───────────────────────────────────────────────────────────────────
class GrantTokensIn(BaseModel):
    amount: int


class SetFieldIn(BaseModel):
    value: bool


# ── Routes ────────────────────────────────────────────────────────────────────
@router.get("/stats")
async def admin_stats(_=Depends(require_admin)):
    stats = await db.get_platform_stats()
    # Convert Decimal/datetime to JSON-safe values
    return _serialize(stats)


@router.get("/users")
async def admin_users(
    search: str = "",
    limit:  int = 100,
    offset: int = 0,
    _=Depends(require_admin),
):
    rows  = await db.list_users(search, limit, offset)
    total = await db.count_users(search)
    return {
        "total":  total,
        "limit":  limit,
        "offset": offset,
        "items":  [_serialize(r) for r in rows],
    }


@router.post("/users/{uid}/grant-tokens")
async def admin_grant_tokens(uid: int, body: GrantTokensIn, _=Depends(require_admin)):
    if body.amount <= 0 or body.amount > 100_000_000:
        raise HTTPException(400, "Invalid amount")
    await db.admin_grant_tokens(uid, body.amount)
    return {"ok": True}


@router.post("/users/{uid}/block")
async def admin_block(uid: int, body: SetFieldIn, _=Depends(require_admin)):
    await db.admin_set_user_field(uid, "is_blocked", body.value)
    return {"ok": True, "is_blocked": body.value}


@router.post("/users/{uid}/admin")
async def admin_make_admin(uid: int, body: SetFieldIn, admin: dict = Depends(require_admin)):
    if uid == admin["id"] and not body.value:
        raise HTTPException(400, "Cannot revoke your own admin rights")
    await db.admin_set_user_field(uid, "is_admin", body.value)
    return {"ok": True, "is_admin": body.value}


@router.delete("/users/{uid}")
async def admin_delete_user(uid: int, admin: dict = Depends(require_admin)):
    if uid == admin["id"]:
        raise HTTPException(400, "Cannot delete your own account here")
    await db.admin_delete_user(uid)
    return {"ok": True}


@router.get("/activity")
async def admin_activity(limit: int = 50, _=Depends(require_admin)):
    rows = await db.get_recent_activity(min(limit, 200))
    return [_serialize(r) for r in rows]


@router.get("/models")
async def admin_model_usage(_=Depends(require_admin)):
    rows = await db.get_model_usage()
    return [_serialize(r) for r in rows]


@router.get("/metrics")
async def admin_metrics(_=Depends(require_admin)):
    return _serialize(await db.get_admin_metrics())


@router.get("/metrics/overview")
async def admin_metrics_overview(_=Depends(require_admin)):
    return _serialize(await db.admin_metrics_overview())


@router.get("/metrics/rag")
async def admin_metrics_rag(_=Depends(require_admin)):
    out = await db.admin_metrics_rag()
    out["slowest_queries"] = [_serialize(r) for r in out["slowest_queries"]]
    return _serialize(out)


@router.get("/metrics/usage")
async def admin_metrics_usage(_=Depends(require_admin)):
    out = await db.admin_metrics_usage()
    out["requests_per_day"] = [_serialize(r) for r in out["requests_per_day"]]
    out["top_users"] = [_serialize(r) for r in out["top_users"]]
    out["top_models"] = [_serialize(r) for r in out["top_models"]]
    return out


@router.get("/metrics/marketing")
async def admin_metrics_marketing(_=Depends(require_admin)):
    out = await db.admin_metrics_marketing()
    out["traffic_sources"] = [_serialize(r) for r in out["traffic_sources"]]
    out["campaign_performance"] = [_serialize(r) for r in out["campaign_performance"]]
    return _serialize(out)


# ── Helper ────────────────────────────────────────────────────────────────────
def _serialize(d) -> dict:
    if d is None:
        return None
    out = {}
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif type(v).__name__ == "Decimal":
            out[k] = float(v)
        elif type(v).__name__ == "UUID":
            out[k] = str(v)
        else:
            out[k] = v
    return out


# ── Analytics routes ──────────────────────────────────────────────────────────
import analytics


@router.get("/analytics/dau")
async def dau(days: int = 30, _=Depends(require_admin)):
    return await analytics.daily_active_users(min(days, 180))


@router.get("/analytics/signups")
async def signups(days: int = 30, _=Depends(require_admin)):
    return await analytics.signups_by_day(min(days, 180))


@router.get("/analytics/messages")
async def msgs(days: int = 30, _=Depends(require_admin)):
    return await analytics.messages_by_day(min(days, 180))


@router.get("/analytics/events")
async def top_events(days: int = 7, _=Depends(require_admin)):
    return await analytics.top_events(min(days, 180), 12)


@router.get("/analytics/funnel")
async def funnel(days: int = 30, _=Depends(require_admin)):
    return await analytics.funnel(min(days, 180))


@router.get("/analytics/features")
async def features(days: int = 30, _=Depends(require_admin)):
    return await analytics.feature_adoption(min(days, 180))


@router.get("/analytics/system")
async def system_metrics(_=Depends(require_admin)):
    """Live in-memory system metrics (HTTP, Gemini, BG tasks)."""
    return analytics.metrics.snapshot()