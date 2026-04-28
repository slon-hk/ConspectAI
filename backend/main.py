import os
import io
import math
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

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
from promts import SYSTEM_PROMPTS, TEMPLATE_META, MODELS, MINDMAP_PROMPT

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
    return jinja.TemplateResponse("pricing.html", {"request": request})


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
    user    = await db.create_user(username, email, pw_hash, auth.TRIAL_TOKENS)
    token   = auth.create_access_token(user["id"])

    # Record consent as an analytics event. The events table is append-only,
    # so this gives a defensible audit trail (timestamp + user id) of when the
    # user accepted the offer and privacy policy.
    analytics.track("signup", user["id"], trial_tokens=auth.TRIAL_TOKENS)
    analytics.track("agreement_accepted", user["id"], offer_version="2026-04-26", privacy_version="2026-04-26")

    return {
        "access_token": token,
        "token_type":   "bearer",
        "user": _safe_user(user),
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
        "user": _safe_user(user),
    }


def _safe_user(u: dict) -> dict:
    return {
        "id":               u["id"],
        "username":         u["username"],
        "email":            u["email"],
        "tokens_remaining": u["tokens_remaining"],
        "is_trial":         u["is_trial"],
        "is_admin":         bool(u.get("is_admin", False)),
        "is_blocked":       bool(u.get("is_blocked", False)),
        "total_spent_usd":  float(u.get("total_spent_usd") or 0),
    }


def _model_name(key: str) -> str:
    """Ensure model name has the required 'models/' prefix for Gemini API."""
    if key.startswith("models/"):
        return key
    return f"models/{key}"


# ── User ──────────────────────────────────────────────────────────────────────
@app.get("/api/user")
async def get_user(uid: int = Depends(current_user_id)):
    user = await db.get_user_by_id(uid)
    if not user:
        raise HTTPException(404, "User not found")
    return _safe_user(user)


# ── Static data ────────────────────────────────────────────────────────────────
@app.get("/api/models")
async def get_models():
    return MODELS


@app.get("/api/templates")
async def get_templates():
    return TEMPLATE_META


# ── Chats ──────────────────────────────────────────────────────────────────────
@app.get("/api/chats")
async def get_chats(uid: int = Depends(current_user_id)):
    rows = await db.get_chats(uid)
    return [_serialize(r) for r in rows]


@app.post("/api/chats")
async def create_chat(
    template: str = Form("deep"),
    model:    str = Form("gemini-3.1-flash-lite-preview"),
    uid:      int = Depends(current_user_id),
):
    tpl = template if template in SYSTEM_PROMPTS else "deep"
    mdl = model    if model    in MODELS          else "gemini-3.1-flash-lite-preview"
    row = await db.create_chat(uid, tpl, mdl)
    analytics.track("chat_created", uid, template=tpl, model=mdl)
    return _serialize(row)


@app.patch("/api/chats/{chat_id}/settings")
async def update_settings(
    chat_id:  str,
    template: str = Form(None),
    model:    str = Form(None),
    uid:      int = Depends(current_user_id),
):
    kwargs = {}
    if template and template in SYSTEM_PROMPTS: kwargs["template"] = template
    if model    and model    in MODELS:          kwargs["model"]    = model
    if not kwargs:
        raise HTTPException(400, "Nothing to update")
    await db.update_chat_settings(chat_id, uid, **kwargs)
    if "template" in kwargs:
        analytics.track("template_switched", uid, chat_id=str(chat_id), template=kwargs["template"])
    if "model" in kwargs:
        analytics.track("model_switched", uid, chat_id=str(chat_id), model=kwargs["model"])
    chat = await db.get_chat(chat_id, uid)
    return _serialize(chat)


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, uid: int = Depends(current_user_id)):
    await db.delete_chat(chat_id, uid)
    return {"ok": True}


# ── Messages ──────────────────────────────────────────────────────────────────
@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: str, uid: int = Depends(current_user_id)):
    chat = await db.get_chat(chat_id, uid)
    if not chat:
        raise HTTPException(404, "Chat not found")
    msgs = await db.get_messages(chat_id)
    return [_serialize_msg(m) for m in msgs]


@app.post("/api/chats/{chat_id}/messages")
async def send_message(
    chat_id:    str,
    bg:         BackgroundTasks,
    content:    str  = Form(""),
    files_json: str  = Form("[]"),
    uid:        int  = Depends(current_user_id),
):
    import json

    if not GEMINI_API_KEY:
        raise HTTPException(500, "GEMINI_API_KEY not set in .env")

    chat = await db.get_chat(chat_id, uid)
    if not chat:
        raise HTTPException(404, "Chat not found")

    user = await db.get_user_by_id(uid)
    if user.get("is_blocked"):
        raise HTTPException(403, "Аккаунт заблокирован администратором")
    if user["tokens_remaining"] <= 0:
        analytics.track("tokens_depleted", uid)
        raise HTTPException(402, "Токены закончились. Пожалуйста, пополните баланс.")

    file_refs = json.loads(files_json)   # [{sha256, original_filename, mime_type, compressed}, ...]

    tpl_key  = chat["template"] or "deep"
    mdl_key  = chat["model"]    or "gemini-3.1-flash-lite-preview"
    if mdl_key not in MODELS: mdl_key = "gemini-3.1-flash-lite-preview"
    sys_p    = SYSTEM_PROMPTS.get(tpl_key, SYSTEM_PROMPTS["deep"])

    # Build Gemini history for memory
    history_rows = await db.get_messages(chat_id)
    gemini_history = []
    for r in history_rows:
        role = "user" if r["role"] == "user" else "model"
        gemini_history.append({"role": role, "parts": [r["content"] or "…"]})

    # Save user message
    user_msg = await db.save_message(
        chat_id, "user", content,
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

    m_info = MODELS[mdl_key]

    # ── User-facing billing: flat rate per model ───────────────────────────
    # Each model has a fixed `tokens_per_request` rate (e.g. 500 / 2000 / 8000).
    # Normally we charge exactly the flat rate regardless of actual API usage.
    # Fallback for huge inputs (large files, very long context): if the API
    # actually consumed more than the rate, we round UP to the next multiple
    # of the rate. The user is never charged a non-multiple of the rate, so
    # the price is always predictable.
    rate          = m_info.get("tokens_per_request", 2000)
    multiplier    = max(1, math.ceil(api_tokens / rate))
    user_tokens   = rate * multiplier        # what we deduct from balance
    extra_charge  = multiplier > 1           # flag for UX warning

    # Real USD cost paid to Google (for analytics / margin calc, not for user)
    cost_usd = round((api_tokens / 1_000_000) * (m_info["cost_in"] + m_info["cost_out"]) / 2, 6)

    # Save assistant message
    asst_msg = await db.save_message(
        chat_id, "assistant", asst_txt,
        tokens=user_tokens, model=mdl_key, cost_usd=cost_usd,
    )
    asst_msg = dict(asst_msg)
    if extra_charge:
        asst_msg["extra_charge_multiplier"] = multiplier
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

    # Deduct from user balance
    await db.deduct_tokens(uid, user_tokens, cost_usd)

    # Track for analytics — keep both numbers so we can later see margin
    analytics.track(
        "message_sent", uid,
        chat_id=str(chat_id), template=tpl_key, model=mdl_key,
        tokens=user_tokens, api_tokens=api_tokens,
        multiplier=multiplier, cost_usd=cost_usd,
        files=len(file_refs),
    )

    title_updates = {}
    if chat["title"] in ("Новый чат", "New Chat") and content:
        title_updates["title"] = content[:55] + ("…" if len(content) > 55 else "")
    title_updates["updated_at"] = datetime.utcnow()
    await db.update_chat_settings(chat_id, uid, **title_updates)

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
    "buy_modal_opened",       # user clicked the "Buy tokens" button
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
    chat = await db.get_chat(chat_id, uid)
    if not chat:
        raise HTTPException(404, "Chat not found")
    bg.add_task(regenerate_mindmap, chat_id)
    analytics.track("mindmap_regenerated", uid, chat_id=str(chat_id))
    return {"ok": True, "queued": True}