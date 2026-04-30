"""Compatibility entrypoint for the current uvicorn/Docker command."""

from app.main import app

__all__ = ["app"]
