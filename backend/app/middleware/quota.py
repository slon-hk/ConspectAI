"""Subscription quota middleware."""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from fastapi import FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import Response

from app.services.quota_service import QuotaService
from app.services.request_metrics_service import RequestMetricsService
from app.services.usage_service import UsageService


def register_subscription_quota_middleware(
    app: FastAPI,
    *,
    decode_token: Callable[[str], int | None],
    quota_service: QuotaService,
    usage_service: UsageService,
    request_metrics_service: RequestMetricsService,
) -> None:
    @app.middleware("http")
    async def subscription_quota_middleware(request: Request, call_next):
        request.state.request_log_id = None
        request.state.current_uid = None
        request.state._metrics_started = time.perf_counter()
        if _needs_quota_check(request.url.path, request.method):
            auth_header = request.headers.get("Authorization", "")
            token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else None
            uid = decode_token(token) if token else None
            if not uid:
                raise HTTPException(status_code=401, detail="Invalid or expired token")
            request.state.current_uid = uid
            quota = await quota_service.check_and_consume_limit(uid, request.url.path)
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
        except Exception as exc:
            if request.state.request_log_id:
                await usage_service.fail_and_refund_request(request.state.request_log_id, str(exc))
                usage = getattr(request.state, "billing_usage", {})
                latency_ms = int((time.perf_counter() - request.state._metrics_started) * 1000)
                await request_metrics_service.log_request_from_usage(
                    request_log_id=request.state.request_log_id,
                    user_id=request.state.current_uid,
                    usage=usage,
                    status="error",
                    error_message=str(exc),
                    latency_ms=latency_ms,
                    session_count_inc=0,
                )
            raise

        if request.state.request_log_id:
            usage = getattr(request.state, "billing_usage", None)
            latency_ms = int((time.perf_counter() - request.state._metrics_started) * 1000)
            if response.status_code >= 400:
                await usage_service.fail_and_refund_request(
                    request.state.request_log_id,
                    f"http_{response.status_code}",
                )
            if usage:
                await request_metrics_service.log_request_from_usage(
                    request_log_id=request.state.request_log_id,
                    user_id=request.state.current_uid,
                    usage=usage,
                    status="success" if response.status_code < 400 else "error",
                    error_message="" if response.status_code < 400 else f"http_{response.status_code}",
                    latency_ms=latency_ms,
                )
                if request.state.current_uid:
                    await request_metrics_service.log_rag_from_usage(
                        user_id=request.state.current_uid,
                        usage=usage,
                        latency_ms=latency_ms,
                    )
        return response


def _needs_quota_check(path: str, method: str) -> bool:
    return method.upper() == "POST" and bool(re.match(r"^/api/chats/[^/]+/messages$", path))
