"""Mindmap background generation orchestration."""

from __future__ import annotations

import logging

from app.services.analytics_tracking_service import AnalyticsTrackingService
from app.services.mindmap_service import MindmapService

logger = logging.getLogger(__name__)


class MindmapGenerationService:
    def __init__(
        self,
        *,
        mindmap_service: MindmapService,
        analytics_tracking_service: AnalyticsTrackingService,
        gemini_api_key: str,
        model_key: str,
        system_prompt: str,
    ) -> None:
        self._mindmap_service = mindmap_service
        self._analytics_tracking_service = analytics_tracking_service
        self._gemini_api_key = gemini_api_key
        self._model_key = model_key
        self._system_prompt = system_prompt

    async def regenerate_background(self, chat_id: str) -> None:
        if not self._gemini_api_key:
            return
        try:
            generated = await self._mindmap_service.regenerate(
                chat_id=chat_id,
                model_name=self._model_name(self._model_key),
                system_prompt=self._system_prompt,
                enabled=bool(self._gemini_api_key),
            )
            if generated:
                self._analytics_tracking_service.increment_mindmap_runs()
        except Exception:
            self._analytics_tracking_service.increment_mindmap_failures()
            logger.exception("mindmap regeneration failed for chat %s", chat_id)

    @staticmethod
    def _model_name(key: str) -> str:
        if key.startswith("models/"):
            return key
        return f"models/{key}"
