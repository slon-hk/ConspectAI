"""POST /api/feedback — user signal collection for the RAG data flywheel."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.repositories.oltp.feedback import FeedbackRepository

_VALID_SIGNALS = frozenset({"thumbs_up", "thumbs_down", "regenerate", "follow_up", "copy_answer"})
_SIGNAL_VALUE: dict[str, int] = {
    "thumbs_up": 1,
    "thumbs_down": -1,
    "regenerate": -1,
    "follow_up": 0,
    "copy_answer": 1,
}


class FeedbackBody(BaseModel):
    trace_id: int | None = None
    signal: str
    comment: str | None = Field(default=None, max_length=2000)
    query_text: str | None = Field(default=None, max_length=4000)
    answer_text: str | None = Field(default=None, max_length=8000)
    chunk_ids: list[str] = Field(default_factory=list, max_length=20)


def create_feedback_router(
    *,
    current_user_id: Callable,
    feedback_repository: FeedbackRepository,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["feedback"])

    @router.post("/feedback", status_code=201)
    async def submit_feedback(
        body: FeedbackBody,
        user_id: int = Depends(current_user_id),
    ) -> dict:
        if body.signal not in _VALID_SIGNALS:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid signal. Must be one of: {sorted(_VALID_SIGNALS)}",
            )

        if body.trace_id is not None:
            owned = await feedback_repository.verify_trace_owner(
                trace_id=body.trace_id,
                user_id=user_id,
            )
            if not owned:
                raise HTTPException(status_code=404, detail="Trace not found")

        feedback_id = await feedback_repository.insert_feedback(
            user_id=user_id,
            trace_id=body.trace_id,
            chat_id=None,
            signal=body.signal,
            signal_value=_SIGNAL_VALUE[body.signal],
            comment=body.comment,
            query_text=body.query_text,
            answer_text=body.answer_text,
            chunk_ids=body.chunk_ids,
        )
        return {"id": feedback_id, "status": "recorded"}

    return router
