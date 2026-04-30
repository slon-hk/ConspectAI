import os
import io
import asyncio
from contextlib import asynccontextmanager
import re
import uuid

import google.generativeai as genai
import PIL.Image
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, BackgroundTasks, status
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from dotenv import load_dotenv

import db
import auth
import storage
import admin
import analytics
import rag_routes
from app.db.pool import database
from app.repositories.oltp import ChatRepository, MessageRepository
from app.services import ChatService
from billing import calculate_cost_units
from promts import SYSTEM_PROMPTS, TEMPLATE_META, MODELS, MINDMAP_PROMPT
from billing_plans import public_plans

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.create_pool()
    # Periodic cleanup of old analytics events (older than 90 days)
    cleanup_task = asyncio.create_task(analytics.cleanup_loop())
    yield
    cleanup_task.cancel()
    await db.close_pool()


app = FastAPI(title="ConspectAI", lifespan=lifespan)
jinja = Jinja2Templates(directory="templates")
app.include_router(admin.router)
app.include_router(rag_routes.router)
chat_service = ChatService(ChatRepository(database), MessageRepository(database))

# Static assets (error-page backgrounds, etc.) — served directly without auth
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── 404 handler ───────────────────────────────────────────────────────────────
@app.exception_handler(404)
async def not_found(request: Request, exc):
    """Pretty 404 page for browser routes; JSON for API endpoints."""
    from fastapi.responses import JSONResponse
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return jinja.TemplateResponse("404.html", {"request": request}, status_code=404)


@app.exception_handler(500)
async def internal_error(request: Request, exc):
    """Pretty 503 page for browser; JSON for API endpoints."""
    from fastapi.responses import JSONResponse
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
    return jinja.TemplateResponse("503.html", {"request": request}, status_code=503)


# ── HTTP metrics middleware ───────────────────────────────────────────────────
@app.middleware("http")
async def http_metrics_middleware(request: Request, call_next):
    import time as _t
    start = _t.perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    except Exception:
        status = 500
        raise
    finally:
        elapsed_ms = (_t.perf_counter() - start) * 1000
        analytics.metrics.record_http(request.url.path, status, elapsed_ms)


def _needs_quota_check(path: str, method: str) -> bool:
    return method.upper() == "POST" and bool(re.match(r"^/api/chats/[^/]+/messages$", path))


@app.middleware("http")
async def subscription_quota_middleware(request: Request, call_next):
    request.state.request_log_id = None
    request.state.current_uid = None
    request.state._metrics_started = __import__("time").perf_counter()
    if _needs_quota_check(request.url.path, request.method):
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else None
        uid = auth.decode_token(token) if token else None
        if not uid:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        request.state.current_uid = uid
        quota = await db.check_and_consume_limit(uid, request.url.path)
        if not quota.get("allowed"):
            return Response(
                content='{"detail":"Доступный объём тарифа закончился","code":"quota_exceeded"}',
                status_code=429,
                media_type="application/json",
            )
        request.state.request_log_id = quota["request_log_id"]
        request.state.usage_remaining = quota["remaining"]
    try:
        response = await call_next(request)
    except Exception as e:
        if request.state.request_log_id:
            await db.fail_and_refund_request(request.state.request_log_id, str(e))
            usage = getattr(request.state, "billing_usage", {})
            latency_ms = int((__import__("time").perf_counter() - request.state._metrics_started) * 1000)
            await db.log_request_metrics(
                request_log_id=request.state.request_log_id,
                user_id=request.state.current_uid,
                model=usage.get("model_name", "unknown"),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                cost_usd=float(usage.get("cost_units", 0)),
                status="error",
                error_message=str(e),
                latency_ms=latency_ms,
                cache_hit=bool(usage.get("cache_hit", False)),
                rag_savings_percent=float(usage.get("savings_pct", 0)),
                session_count_inc=0,
            )
        raise

    if request.state.request_log_id:
        usage = getattr(request.state, "billing_usage", None)
        latency_ms = int((__import__("time").perf_counter() - request.state._metrics_started) * 1000)
        if response.status_code >= 400:
            await db.fail_and_refund_request(request.state.request_log_id, f"http_{response.status_code}")
        if usage:
            await db.log_request_metrics(
                request_log_id=request.state.request_log_id,
                user_id=request.state.current_uid,
                model=usage.get("model_name", "unknown"),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                cost_usd=float(usage.get("cost_units", 0)),
                status="success" if response.status_code < 400 else "error",
                error_message="" if response.status_code < 400 else f"http_{response.status_code}",
                latency_ms=latency_ms,
                cache_hit=bool(usage.get("cache_hit", False)),
                rag_savings_percent=float(usage.get("savings_pct", 0)),
            )
            rag_meta = usage.get("rag_metrics")
            if rag_meta and request.state.current_uid:
                await db.insert_rag_metric(
                    user_id=request.state.current_uid,
                    query=rag_meta.get("query", ""),
                    chunks_used=int(rag_meta.get("chunks_used", 0)),
                    context_tokens=int(rag_meta.get("context_tokens", 0)),
                    total_tokens=int(usage.get("total_tokens", 0)),
                    estimated_tokens_no_rag=int(rag_meta.get("estimated_tokens_no_rag", 0)),
                    savings_percent=float(usage.get("savings_pct", 0)),
                    latency_ms=int(rag_meta.get("latency_ms", latency_ms)),
                    cache_hit=bool(usage.get("cache_hit", False)),
                )
    return response


# ── Auth dependency ────────────────────────────────────────────────────────────
async def current_user_id(token: str = Depends(auth.oauth2)) -> int:
    """Resolve the JWT to a user id, then verify the user still exists.

    A token is "valid" if it's signed correctly and not expired, but the user
    it points to may have been deleted (or the DB may have been recreated).
    In either case we return 401 so the frontend forces a fresh login instead
    of silently sending requests for a ghost user.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    uid = auth.decode_token(token)
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = await db.get_user_by_id(uid)
    if not user:
        # Token's signature is valid but the user no longer exists.
        raise HTTPException(status_code=401, detail="User no longer exists — please log in again")
    if user.get("is_blocked"):
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован администратором")
    return uid


# ── Pydantic schemas ───────────────────────────────────────────────────────────
class RegisterIn(BaseModel):
    username: str
    email:    str
    password: str
    # Acceptance of public offer + privacy policy. Required by Russian law to
    # establish a contract with the user — without this, technically no договор
    # is concluded. UI enforces the checkbox; backend enforces it as a final
    # safety net in case someone bypasses the UI (DevTools / curl).
    agreement: bool = False


class LoginIn(BaseModel):
    email: str
    password: str


# ── Pages ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    await db.insert_funnel_event(user_id=None, event_name="visit", metadata={"path": "/"})
    return jinja.TemplateResponse("landing.html", {"request": request})


@app.get("/app", response_class=HTMLResponse)
async def app_page(request: Request):
    return jinja.TemplateResponse("index.html", {"request": request})


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return jinja.TemplateResponse("admin.html", {"request": request})


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return jinja.TemplateResponse("privacy.html", {"request": request})


@app.get("/offer", response_class=HTMLResponse)
async def offer_page(request: Request):
    return jinja.TemplateResponse("offer.html", {"request": request})


@app.get("/contacts", response_class=HTMLResponse)
async def contacts_page(request: Request):
    return jinja.TemplateResponse("contacts.html", {"request": request})


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    plans = []
    for plan in public_plans():
        price = int(plan["price_rub"])
        estimated_requests = int(plan.get("estimated_monthly_requests", 0) or 0)
        plans.append({
            **plan,
            "price_label": "₽0" if price == 0 else f"₽{price:,}".replace(",", " "),
            "estimated_requests_label": (
                "без включённых AI-запросов"
                if estimated_requests <= 0
                else f"≈ {estimated_requests:,}".replace(",", " ") + " запросов в месяц"
            ),
            "featured": plan["plan_key"] == "plus",
            "cta_label": "Начать бесплатно" if plan["plan_key"] == "free" else f"Выбрать {plan['display_name']}",
        })
    return jinja.TemplateResponse("pricing.html", {"request": request, "plans": plans})


# ── Auth endpoints ────────────────────────────────────────────────────────────
@app.post("/api/auth/register")
async def register(body: RegisterIn):
    username = body.username.strip()
    email    = body.email.strip().lower()
    password = body.password

    if len(username) < 2:
        raise HTTPException(400, "Имя пользователя слишком короткое (мин. 2 символа)")
    if len(password) < 6:
        raise HTTPException(400, "Пароль слишком короткий (мин. 6 символов)")
    if not body.agreement:
        raise HTTPException(400, "Для регистрации необходимо принять условия оферты и политики конфиденциальности")

    if await db.get_user_by_email(email):
        raise HTTPException(409, "Пользователь с таким email уже существует")
    if await db.get_user_by_username(username):
        raise HTTPException(409, "Это имя пользователя уже занято")

    pw_hash = auth.hash_password(password)
    user    = await db.create_user(username, email, pw_hash)
    token   = auth.create_access_token(user["id"])

    # Record consent as an analytics event. The events table is append-only,
    # so this gives a defensible audit trail (timestamp + user id) of when the
    # user accepted the offer and privacy policy.
    analytics.track("signup", user["id"])
    await db.insert_funnel_event(user_id=user["id"], event_name="signup", metadata={"channel": "auth_register"})
    analytics.track("agreement_accepted", user["id"], offer_version="2026-04-26", privacy_version="2026-04-26")

    return {
        "access_token": token,
        "token_type":   "bearer",
        "user": await _safe_user(user),
    }


@app.post("/api/auth/login")
async def login(body: LoginIn):
    email = body.email.strip().lower()
    user  = await db.get_user_by_email(email)

    if not user or not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Неверный email или пароль")

    if user.get("is_blocked"):
        raise HTTPException(403, "Аккаунт заблокирован администратором")

    token = auth.create_access_token(user["id"])
    analytics.track("login", user["id"])
    return {
        "access_token": token,
        "token_type":   "bearer",
        "user": await _safe_user(user),
    }


async def _safe_user(u: dict) -> dict:
    usage = await db.get_user_usage_snapshot(u["id"])
    return {
        "id":               u["id"],
        "username":         u["username"],
        "email":            u["email"],
        "subscription_id":  u.get("subscription_id"),
        "plan_key":         usage.get("plan_key", "free"),
        "subscription_name": usage.get("subscription_name", "Free"),
        "usage":            usage,
        "is_admin":         bool(u.get("is_admin", False)),
        "is_blocked":       bool(u.get("is_blocked", False)),
        "total_spent_usd":  float(u.get("total_spent_usd") or 0),
    }


def _model_name(key: str) -> str:
    """Ensure model name has the required 'models/' prefix for Gemini API."""
    if key.startswith("models/"):
        return key
    return f"models/{key}"


def _validate_chat_id(chat_id: str) -> str:
    try:
        return str(uuid.UUID(str(chat_id)))
    except Exception:
        raise HTTPException(400, "Invalid chat_id")


# ── User ──────────────────────────────────────────────────────────────────────
@app.get("/api/user")
async def get_user(uid: int = Depends(current_user_id)):
    user = await db.get_user_by_id(uid)
    if not user:
        raise HTTPException(404, "User not found")
    return await _safe_user(user)


@app.get("/api/usage")
async def get_usage(uid: int = Depends(current_user_id)):
    remaining = await db.get_user_usage_snapshot(uid)
    return remaining


@app.get("/usage")
async def get_usage_public(uid: int = Depends(current_user_id)):
    return await db.get_user_usage_snapshot(uid)


@app.get("/admin/metrics")
async def get_admin_metrics_public(_=Depends(admin.require_admin)):
    return await db.get_admin_metrics()


@app.get("/admin/metrics/overview")
async def get_admin_metrics_overview_public(_=Depends(admin.require_admin)):
    return await db.admin_metrics_overview()


@app.get("/admin/metrics/rag")
async def get_admin_metrics_rag_public(_=Depends(admin.require_admin)):
    return await db.admin_metrics_rag()


@app.get("/admin/metrics/usage")
async def get_admin_metrics_usage_public(_=Depends(admin.require_admin)):
    return await db.admin_metrics_usage()


@app.get("/admin/metrics/marketing")
async def get_admin_metrics_marketing_public(_=Depends(admin.require_admin)):
    return await db.admin_metrics_marketing()


# ── Static data ────────────────────────────────────────────────────────────────
@app.get("/api/models")
async def get_models():
    return {
        key: {
            "name": info["name"],
            "desc": info["desc"],
            "speed": info["speed"],
            "recommended": bool(info.get("recommended", False)),
        }
        for key, info in MODELS.items()
    }


@app.get("/api/templates")
async def get_templates():
    return TEMPLATE_META


@app.get("/api/subscription-plans")
async def get_subscription_plans():
    return public_plans()


# ── Chats ──────────────────────────────────────────────────────────────────────
@app.get("/api/chats")
async def get_chats(uid: int = Depends(current_user_id)):
    rows = await chat_service.list_chats(uid)
    return [_serialize(r) for r in rows]


@app.post("/api/chats")
async def create_chat(
    template: str = Form("deep"),
    model:    str = Form("gemini-3.1-flash-lite-preview"),
    uid:      int = Depends(current_user_id),
):
    row, tpl, mdl = await chat_service.create_chat(
        user_id=uid,
        template=template,
        model=model,
        allowed_templates=SYSTEM_PROMPTS,
        allowed_models=MODELS,
        default_template="deep",
        default_model="gemini-3.1-flash-lite-preview",
    )
    analytics.track("chat_created", uid, template=tpl, model=mdl)
    return _serialize(row)


@app.patch("/api/chats/{chat_id}/settings")
async def update_settings(
    chat_id:  str,
    template: str = Form(None),
    model:    str = Form(None),
    uid:      int = Depends(current_user_id),
):
    chat_id = _validate_chat_id(chat_id)
    try:
        chat, updates = await chat_service.update_chat_settings(
            chat_id=chat_id,
            user_id=uid,
            template=template,
            model=model,
            allowed_templates=SYSTEM_PROMPTS,
            allowed_models=MODELS,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not chat:
        raise HTTPException(404, "Chat not found")
    if "template" in updates:
        analytics.track("template_switched", uid, chat_id=str(chat_id), template=updates["template"])
    if "model" in updates:
        analytics.track("model_switched", uid, chat_id=str(chat_id), model=updates["model"])
    return _serialize(chat)


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, uid: int = Depends(current_user_id)):
    chat_id = _validate_chat_id(chat_id)
    await chat_service.delete_chat(chat_id=chat_id, user_id=uid)
    return {"ok": True}


# ── Messages ──────────────────────────────────────────────────────────────────
@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: str, uid: int = Depends(current_user_id)):
    chat_id = _validate_chat_id(chat_id)
    msgs = await chat_service.list_messages_for_user_chat(chat_id=chat_id, user_id=uid)
    if msgs is None:
        raise HTTPException(404, "Chat not found")
    return [_serialize_msg(m) for m in msgs]


@app.post("/api/chats/{chat_id}/messages")
async def send_message(
    chat_id:    str,
    request:    Request,
    bg:         BackgroundTasks,
    content:    str  = Form(""),
    files_json: str  = Form("[]"),
    uid:        int  = Depends(current_user_id),
):
    chat_id = _validate_chat_id(chat_id)
    import json

    if not GEMINI_API_KEY:
        raise HTTPException(500, "GEMINI_API_KEY not set in .env")

    chat = await chat_service.get_chat(chat_id=chat_id, user_id=uid)
    if not chat:
        raise HTTPException(404, "Chat not found")

    user = await db.get_user_by_id(uid)
    if user.get("is_blocked"):
        raise HTTPException(403, "Аккаунт заблокирован администратором")

    file_refs = json.loads(files_json)   # [{sha256, original_filename, mime_type, compressed}, ...]

    tpl_key  = chat["template"] or "deep"
    mdl_key  = chat["model"]    or "gemini-3.1-flash-lite-preview"
    if mdl_key not in MODELS: mdl_key = "gemini-3.1-flash-lite-preview"
    sys_p    = SYSTEM_PROMPTS.get(tpl_key, SYSTEM_PROMPTS["deep"])

    # Build Gemini history for memory
    history_rows = await chat_service.list_messages(chat_id=chat_id)
    gemini_history = []
    for r in history_rows:
        role = "user" if r["role"] == "user" else "model"
        gemini_history.append({"role": role, "parts": [r["content"] or "…"]})

    # Save user message
    user_msg = await chat_service.save_message(
        chat_id=chat_id,
        role="user",
        content=content,
        file_metas=[{"sha256": f["sha256"], "original_filename": f["original_filename"]}
                    for f in file_refs],
    )

    # Build parts for current turn
    parts: list = [content] if content else []
    for f in file_refs:
        meta = await db.get_file_meta(f["sha256"])
        if not meta:
            continue
        try:
            raw = storage.read_file(meta["sha256"], meta["compressed"])
            if meta["mime_type"].startswith("image/"):
                parts.append(PIL.Image.open(io.BytesIO(raw)))
            else:
                # Upload to Gemini Files API for non-images
                buf = io.BytesIO(raw)
                buf.name = f["original_filename"]
                uploaded = genai.upload_file(buf, mime_type=meta["mime_type"])
                parts.append(uploaded)
        except Exception as e:
            print(f"File attach error: {e}")

    if not parts:
        raise HTTPException(400, "Нет содержимого для отправки")

    # ── RAG path: if chat has a linked course, use retrieval ──────────────
    course_id: str | None = chat.get("course_id")
    rag_images: list[dict] = []
    rag_result = {}
    if file_refs:
        try:
            import rag as rag_engine
            auto_course = await rag_engine.ensure_chat_course_and_ingest_uploads(
                chat_id=chat_id,
                user_id=uid,
                file_refs=file_refs,
            )
            if auto_course and not course_id:
                course_id = auto_course
        except Exception as e:
            print(f"[rag] auto-ingest failed: {e}")

    if course_id:
        import rag as rag_engine
        rag_result = await rag_engine.rag_query(
            query=content or " ".join(str(p) for p in parts if isinstance(p, str)),
            course_id=str(course_id),
            system_prompt=sys_p,
            model_name=mdl_key,
            conversation_history=gemini_history,
        )
        asst_txt   = rag_result["answer"]
        rag_images = rag_result["images"]
        cache_hit = bool(rag_result.get("from_cache"))

        # For billing: RAG adds ~5K input tokens worth of context.
        # We surface this as the minimum multiplier being at least 1,
        # but the flat-rate logic below will still apply.
        api_tokens_override = rag_result.get("api_tokens", None)

        # Record whether we found sources (for analytics)
        analytics.track(
            "rag_query", uid,
            chat_id=str(chat_id), course_id=str(course_id),
            sources_found=rag_result["sources_found"],
            from_cache=rag_result["from_cache"],
        )

    else:
        # ── Standard (non-RAG) Gemini path ────────────────────────────────
        model_obj  = genai.GenerativeModel(model_name=_model_name(mdl_key), system_instruction=sys_p)
        session    = model_obj.start_chat(history=gemini_history)

        loop       = asyncio.get_event_loop()
        import time as _t
        _gem_start = _t.perf_counter()
        try:
            response = await loop.run_in_executor(None, lambda: session.send_message(parts))
            analytics.metrics.record_gemini(mdl_key, (_t.perf_counter() - _gem_start) * 1000, ok=True)
        except Exception as e:
            analytics.metrics.record_gemini(mdl_key, (_t.perf_counter() - _gem_start) * 1000, ok=False)
            analytics.track("gemini_error", uid, model=mdl_key, error=str(e)[:200])
            raise

        asst_txt = response.text
        api_tokens_override = None
        cache_hit = False

        try:    api_tokens_raw = response.usage_metadata.total_token_count
        except: api_tokens_raw = max(1, len(asst_txt) // 4)

    # ── Billing: flat rate per model ───────────────────────────────────────
    if not course_id:
        api_tokens = api_tokens_raw  # type: ignore[name-defined]
    else:
        # RAG: estimate tokens = context (3000) + answer length
        api_tokens = 3000 + max(1, len(asst_txt) // 4)
    if api_tokens_override is not None:
        api_tokens = api_tokens_override

    # Internal cost accounting (user does not see token billing).
    input_tokens = max(1, len(content or "") // 4)
    context_tokens = rag_result.get("context_tokens", 0) if course_id else 0
    output_tokens = max(1, len(asst_txt or "") // 4)
    total_tokens = input_tokens + context_tokens + output_tokens
    estimated_without_rag = rag_result.get("estimated_without_rag_tokens", total_tokens) if course_id else total_tokens
    actual_with_rag = rag_result.get("actual_with_rag_tokens", total_tokens) if course_id else total_tokens
    savings_pct = 0.0
    if estimated_without_rag > 0:
        savings_pct = round(max((estimated_without_rag - actual_with_rag) / estimated_without_rag, 0) * 100, 3)
    cost_usd = calculate_cost_units(mdl_key, input_tokens, output_tokens, context_tokens)
    if cache_hit:
        cost_usd = round(cost_usd * 0.01, 8)

    # Save assistant message
    asst_msg = await chat_service.save_message(
        chat_id=chat_id,
        role="assistant",
        content=asst_txt,
        tokens=total_tokens,
        model=mdl_key,
        cost_usd=cost_usd,
    )
    asst_msg = dict(asst_msg)
    if rag_images:
        # Surface retrieved images to the frontend (served via /api/rag/images/{id})
        asst_msg["rag_images"] = [
            {
                "id":       img["id"],
                "caption":  img["caption"],
                "url":      f"/api/rag/images/{img['id']}",
                "mime":     img["mime_type"],
            }
            for img in rag_images
        ]

    # Track for analytics — keep internal usage figures
    analytics.track(
        "message_sent", uid,
        chat_id=str(chat_id), template=tpl_key, model=mdl_key,
        tokens=total_tokens, api_tokens=api_tokens,
        cost_usd=cost_usd,
        files=len(file_refs),
    )
    request.state.billing_usage = {
        "model_name": mdl_key,
        "cache_hit": cache_hit,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "context_tokens": context_tokens,
        "total_tokens": total_tokens,
        "estimated_no_rag": estimated_without_rag,
        "actual_with_rag": actual_with_rag,
        "savings_pct": savings_pct,
        "cost_units": cost_usd,
        "status": "completed",
        "rag_metrics": {
            "query": content or "",
            "chunks_used": rag_result.get("chunks_used", 0) if course_id else 0,
            "context_tokens": context_tokens,
            "estimated_tokens_no_rag": estimated_without_rag,
            "latency_ms": rag_result.get("latency_ms", 0) if course_id else 0,
        } if course_id else None,
    }

    await chat_service.update_title_after_message(
        chat_id=chat_id,
        user_id=uid,
        current_title=chat["title"],
        content=content,
    )

    # Schedule background mindmap refresh — doesn't block the response
    bg.add_task(regenerate_mindmap, chat_id)

    return _serialize_msg(asst_msg)


# ── File upload ────────────────────────────────────────────────────────────────
@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    uid:  int        = Depends(current_user_id),
):
    raw  = await file.read()
    mime = file.content_type or storage.guess_mime(file.filename)

    meta = storage.store_file(raw, mime)
    await db.register_file(
        meta["sha256"], mime, meta["compressed"],
        meta["original_size"], meta["stored_size"],
    )

    saved_kb    = round((meta["original_size"] - meta["stored_size"]) / 1024, 1)
    compression = round((1 - meta["stored_size"] / max(meta["original_size"], 1)) * 100, 1)

    analytics.track(
        "file_uploaded", uid,
        mime=mime, size=meta["original_size"],
        compressed=meta["compressed"], saved_kb=saved_kb,
    )

    return {
        "sha256":           meta["sha256"],
        "original_filename": file.filename,
        "mime_type":        mime,
        "compressed":       meta["compressed"],
        "original_size":    meta["original_size"],
        "stored_size":      meta["stored_size"],
        "saved_kb":         saved_kb,
        "compression_pct":  compression,
        # preview URL for images
        "preview_url": f"/api/files/{meta['sha256']}/raw" if mime.startswith("image/") else None,
    }


@app.get("/api/files/{sha256}/raw")
async def serve_file(sha256: str, uid: int = Depends(current_user_id)):
    meta = await db.get_file_meta(sha256)
    if not meta:
        raise HTTPException(404, "File not found")
    raw = storage.read_file(meta["sha256"], meta["compressed"])
    return Response(content=raw, media_type=meta["mime_type"])


# ── Client-side analytics endpoint ─────────────────────────────────────────────
# Only events on this allowlist can be reported by the browser. This blocks
# arbitrary clients from injecting fake events into the analytics table.
CLIENT_EVENT_ALLOWLIST = {
    "landing_cta_click",      # which CTA on the landing page was clicked
    "buy_modal_opened",       # user opened the pricing/plan modal
    "export_md",              # user downloaded a chat as Markdown
    "export_pdf",             # user opened the PDF export view
    "settings_opened",        # user opened the settings modal
}


class TrackIn(BaseModel):
    event: str
    props: dict = {}


@app.post("/api/track")
async def client_track(
    body:  TrackIn,
    token: str = Depends(auth.oauth2),
):
    if body.event not in CLIENT_EVENT_ALLOWLIST:
        raise HTTPException(400, f"Unknown event: {body.event}")
    # User is optional — landing page events come from anonymous visitors
    uid = auth.decode_token(token) if token else None
    # Sanitize props: strip nested structures, limit size
    safe_props = {}
    for k, v in (body.props or {}).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            safe_props[str(k)[:40]] = v if not isinstance(v, str) else v[:200]
    analytics.track(body.event, uid, **safe_props)
    return {"ok": True}


# ── Helpers ────────────────────────────────────────────────────────────────────
def _serialize(row: dict) -> dict:
    """Convert asyncpg row (with UUID, datetime) to JSON-safe dict."""
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "__str__") and type(v).__name__ in ("UUID",):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def _serialize_msg(m: dict) -> dict:
    s = _serialize(m)
    if "files" in s:
        s["files"] = [_serialize(f) for f in (s["files"] or [])]
    if "cost_usd" in s and s["cost_usd"] is not None:
        s["cost_usd"] = float(s["cost_usd"])
    return s


# ── Mindmap auto-generation ────────────────────────────────────────────────────
MINDMAP_MODEL = "gemini-2.5-flash-lite"   # cheapest model — platform-paid feature

async def regenerate_mindmap(chat_id: str):
    """
    Build/refresh the topic mindmap for a chat. Runs in the background after
    each assistant reply. Uses Flash Lite to keep cost negligible. Does NOT
    deduct from the user's token balance — this is a platform feature.
    """
    if not GEMINI_API_KEY:
        return
    try:
        msgs = await db.get_messages(chat_id)
        if len(msgs) < 2:                   # need at least one full exchange
            return

        existing = await db.get_mindmap(chat_id)
        existing_md = existing["markdown"] if existing else ""

        # Build conversation digest (last ~12 messages, truncated)
        convo_parts = []
        for m in msgs[-12:]:
            role = "User" if m["role"] == "user" else "Tutor"
            content = (m["content"] or "")[:1500]
            convo_parts.append(f"--- {role} ---\n{content}")
        convo = "\n\n".join(convo_parts)

        prompt = (
            f"EXISTING MINDMAP:\n{existing_md or '(empty — first generation)'}\n\n"
            f"=== CONVERSATION ===\n{convo}\n\n"
            f"Now produce the updated mindmap."
        )

        model = genai.GenerativeModel(model_name=_model_name(MINDMAP_MODEL), system_instruction=MINDMAP_PROMPT)
        loop  = asyncio.get_event_loop()
        resp  = await loop.run_in_executor(None, lambda: model.generate_content(prompt))
        text  = (resp.text or "").strip()

        # Strip code fences if the model wrapped output despite instructions
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        if text:
            await db.save_mindmap(chat_id, text)
            analytics.metrics.bg_mindmap_runs += 1
    except Exception as e:
        analytics.metrics.bg_mindmap_failed += 1
        print(f"[mindmap] error for chat {chat_id}: {e}")


@app.get("/api/chats/{chat_id}/mindmap")
async def fetch_mindmap(chat_id: str, uid: int = Depends(current_user_id)):
    chat_id = _validate_chat_id(chat_id)
    chat = await db.get_chat(chat_id, uid)
    if not chat:
        raise HTTPException(404, "Chat not found")
    mm = await db.get_mindmap(chat_id)
    analytics.track("mindmap_opened", uid, chat_id=str(chat_id), has_content=bool(mm))
    if not mm:
        return {"markdown": "", "updated_at": None}
    return {
        "markdown":   mm["markdown"],
        "updated_at": mm["updated_at"].isoformat() if mm["updated_at"] else None,
    }


@app.post("/api/chats/{chat_id}/mindmap/regenerate")
async def force_regenerate_mindmap(
    chat_id: str,
    bg:      BackgroundTasks,
    uid:     int = Depends(current_user_id),
):
    chat_id = _validate_chat_id(chat_id)
    chat = await db.get_chat(chat_id, uid)
    if not chat:
        raise HTTPException(404, "Chat not found")
    bg.add_task(regenerate_mindmap, chat_id)
    analytics.track("mindmap_regenerated", uid, chat_id=str(chat_id))
    return {"ok": True, "queued": True}
