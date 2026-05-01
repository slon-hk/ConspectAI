"""Compatibility wrapper for the AI prompt/model catalog."""

from app.infrastructure.ai.model_catalog import (
    BASE,
    MINDMAP_PROMPT,
    MODELS,
    SYSTEM_PROMPTS,
    TEMPLATE_META,
)

__all__ = [
    "BASE",
    "MINDMAP_PROMPT",
    "MODELS",
    "SYSTEM_PROMPTS",
    "TEMPLATE_META",
]
