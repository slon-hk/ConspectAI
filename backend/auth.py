import os
from datetime import datetime, timedelta
from typing import Optional
import hashlib
import secrets

import httpx
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

SECRET_KEY = os.getenv("SECRET_KEY_JWT", "change-this-to-a-long-random-secret-in-production")
ALGORITHM  = "HS256"
TOKEN_EXPIRE_DAYS = 30

pwd_ctx   = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2    = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", "no-reply@yourdomain.com")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")

def hash_password(plain: str) -> str:
    return pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)


def create_access_token(user_id: int) -> str:
    expire  = datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[int]:
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid  = data.get("sub")
        return int(uid) if uid else None
    except JWTError:
        return None


async def get_current_user(token: str = Depends(oauth2), db=None):
    """Dependency — resolves to user row. Import and use in routes."""
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    uid = decode_token(token)
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return uid


# ── Cloudflare Turnstile ─────────────────────────────────────────────────────
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

async def verify_captcha(token: str) -> bool:
    """Verify Cloudflare Turnstile captcha token."""
    secret = os.getenv("TURNSTILE_SECRET")
    if not secret:
        # If no secret configured, skip verification (dev mode)
        return True

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                TURNSTILE_VERIFY_URL,
                data={
                    "secret": secret,
                    "response": token,
                },
            )
            result = resp.json()
            return result.get("success", False)
    except Exception:
        # Network failure or timeout — fail closed
        return False

def make_verification_token():
    return secrets.token_urlsafe(32)

def hash_token(token: str):
    return hashlib.sha256(token.encode()).hexdigest()

async def send_verification_email(email: str, token: str):
    url = "https://api.resend.com/emails"

    verify_link = f"https://yourdomain.com/api/auth/verify-email?token={token}"

    async with httpx.AsyncClient() as client:
        await client.post(
            url,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": EMAIL_FROM,
                "to": email,
                "subject": "Verify your email",
                "html": f"""
                    <h3>Verify your email</h3>
                    <p>Click below:</p>
                    <a href="{verify_link}">Verify Email</a>
                """
            }
        )
