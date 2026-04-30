"""
Admin API — routes mounted at /api/admin/*.
Access controlled by the require_admin dependency.

To grant admin privileges to the first user:
    docker compose exec db psql -U orion -d orion \
        -c "UPDATE users SET is_admin=true WHERE email='you@example.com';"
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
import auth
from app.db.pool import database
from app.repositories.olap import AdminReportRepository
from app.repositories.oltp import AdminUserRepository
from app.services import AdminMetricsService, AdminUserService
from billing_plans import PLAN_KEYS

router = APIRouter(prefix="/api/admin", tags=["admin"])
admin_metrics_service = AdminMetricsService(AdminReportRepository(database))
admin_user_service = AdminUserService(AdminUserRepository(database))


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
class SetPlanIn(BaseModel):
    plan_key: str


class SetFieldIn(BaseModel):
    value: bool


# ── Routes ────────────────────────────────────────────────────────────────────
@router.get("/stats")
async def admin_stats(_=Depends(require_admin)):
    stats = await admin_metrics_service.platform_stats()
    # Convert Decimal/datetime to JSON-safe values
    return _serialize(stats)


@router.get("/users")
async def admin_users(
    search: str = "",
    limit:  int = 100,
    offset: int = 0,
    _=Depends(require_admin),
):
    result = await admin_user_service.list_users(search=search, limit=limit, offset=offset)
    result["items"] = [_serialize(r) for r in result["items"]]
    return result


@router.post("/users/{uid}/plan")
async def admin_set_plan(uid: int, body: SetPlanIn, _=Depends(require_admin)):
    if body.plan_key not in PLAN_KEYS:
        raise HTTPException(400, "Unknown plan")
    updated = await admin_user_service.set_plan(user_id=uid, plan_key=body.plan_key)
    if not updated:
        raise HTTPException(404, "User or plan not found")
    return {"ok": True, "plan_key": body.plan_key}


@router.post("/users/{uid}/block")
async def admin_block(uid: int, body: SetFieldIn, _=Depends(require_admin)):
    await admin_user_service.set_blocked(user_id=uid, is_blocked=body.value)
    return {"ok": True, "is_blocked": body.value}


@router.post("/users/{uid}/admin")
async def admin_make_admin(uid: int, body: SetFieldIn, admin: dict = Depends(require_admin)):
    if uid == admin["id"] and not body.value:
        raise HTTPException(400, "Cannot revoke your own admin rights")
    await admin_user_service.set_admin(user_id=uid, is_admin=body.value)
    return {"ok": True, "is_admin": body.value}


@router.delete("/users/{uid}")
async def admin_delete_user(uid: int, admin: dict = Depends(require_admin)):
    if uid == admin["id"]:
        raise HTTPException(400, "Cannot delete your own account here")
    await admin_user_service.delete_user(user_id=uid)
    return {"ok": True}


@router.get("/activity")
async def admin_activity(limit: int = 50, _=Depends(require_admin)):
    rows = await admin_metrics_service.recent_activity(min(limit, 200))
    return [_serialize(r) for r in rows]


@router.get("/models")
async def admin_model_usage(_=Depends(require_admin)):
    rows = await admin_metrics_service.model_usage()
    return [_serialize(r) for r in rows]


@router.get("/metrics")
async def admin_metrics(_=Depends(require_admin)):
    return _serialize(await admin_metrics_service.admin_metrics())


@router.get("/metrics/overview")
async def admin_metrics_overview(_=Depends(require_admin)):
    return _serialize(await admin_metrics_service.overview())


@router.get("/metrics/rag")
async def admin_metrics_rag(_=Depends(require_admin)):
    out = await admin_metrics_service.rag()
    out["slowest_queries"] = [_serialize(r) for r in out["slowest_queries"]]
    return _serialize(out)


@router.get("/metrics/usage")
async def admin_metrics_usage(_=Depends(require_admin)):
    out = await admin_metrics_service.usage()
    out["requests_per_day"] = [_serialize(r) for r in out["requests_per_day"]]
    out["top_users"] = [_serialize(r) for r in out["top_users"]]
    out["top_models"] = [_serialize(r) for r in out["top_models"]]
    return out


@router.get("/metrics/marketing")
async def admin_metrics_marketing(_=Depends(require_admin)):
    out = await admin_metrics_service.marketing()
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
