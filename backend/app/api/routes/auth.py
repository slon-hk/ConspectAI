"""Authentication API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.analytics_tracking_service import AnalyticsTrackingService
from app.services.auth_service import (
    AgreementRequiredError,
    AuthAccountBlockedError,
    AuthService,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    PasswordTooShortError,
    UsernameAlreadyExistsError,
    UsernameTooShortError,
)
from app.services.funnel_service import FunnelService


class RegisterIn(BaseModel):
    username: str
    email: str
    password: str
    agreement: bool = False


class LoginIn(BaseModel):
    email: str
    password: str


def create_auth_router(
    *,
    auth_service: AuthService,
    analytics_tracking_service: AnalyticsTrackingService,
    funnel_service: FunnelService,
) -> APIRouter:
    router = APIRouter(tags=["auth"])

    @router.post("/api/auth/register")
    async def register(body: RegisterIn):
        try:
            result = await auth_service.register(
                username=body.username,
                email=body.email,
                password=body.password,
                agreement=body.agreement,
            )
        except (UsernameTooShortError, PasswordTooShortError, AgreementRequiredError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except (EmailAlreadyExistsError, UsernameAlreadyExistsError) as exc:
            raise HTTPException(409, str(exc)) from exc

        user = result.pop("raw_user")
        analytics_tracking_service.track("signup", user["id"])
        await funnel_service.record_signup(user_id=user["id"], channel="auth_register")
        analytics_tracking_service.track(
            "agreement_accepted",
            user["id"],
            offer_version="2026-04-26",
            privacy_version="2026-04-26",
        )
        return result

    @router.post("/api/auth/login")
    async def login(body: LoginIn):
        try:
            result = await auth_service.login(email=body.email, password=body.password)
        except InvalidCredentialsError as exc:
            raise HTTPException(401, str(exc)) from exc
        except AuthAccountBlockedError as exc:
            raise HTTPException(403, str(exc)) from exc

        user = result.pop("raw_user")
        analytics_tracking_service.track("login", user["id"])
        return result

    return router
