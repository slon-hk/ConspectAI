"""Compatibility entrypoint for the current uvicorn/Docker command."""

from app.main import app, create_app

__all__ = ["app", "create_app"]
