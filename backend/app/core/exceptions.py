"""Application exception handler registration."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates


def register_exception_handlers(app: FastAPI, templates: Jinja2Templates) -> None:
    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        """Pretty 404 page for browser routes; JSON for API endpoints."""
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

    @app.exception_handler(500)
    async def internal_error(request: Request, exc):
        """Pretty 503 page for browser; JSON for API endpoints."""
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
        return templates.TemplateResponse("503.html", {"request": request}, status_code=503)
