"""Public catalog/static data service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


class CatalogService:
    def __init__(
        self,
        *,
        models: Mapping[str, Mapping[str, Any]],
        template_meta: Mapping[str, Mapping[str, Any]],
        public_plans: Callable[[], list[dict[str, Any]]],
    ) -> None:
        self._models = models
        self._template_meta = template_meta
        self._public_plans = public_plans

    def public_models(self) -> dict:
        return {
            key: {
                "name": info["name"],
                "desc": info["desc"],
                "speed": info["speed"],
                "recommended": bool(info.get("recommended", False)),
            }
            for key, info in self._models.items()
        }

    def templates(self) -> Mapping[str, Mapping[str, Any]]:
        return self._template_meta

    def subscription_plans(self) -> list[dict[str, Any]]:
        return self._public_plans()
