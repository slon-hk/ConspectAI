"""AI chat turn orchestration service."""

from __future__ import annotations

import asyncio
import io
import json
import time
from collections.abc import Mapping
from typing import Any

import google.generativeai as genai
import PIL.Image

import analytics
import rag as rag_engine
import storage
from app.repositories.oltp import FileRepository
from app.services.chat_service import ChatService
from app.services.user_service import UserService
from billing import calculate_cost_units


class AiChatServiceError(Exception):
    pass


class GeminiApiKeyMissingError(AiChatServiceError):
    pass


class ChatNotFoundError(AiChatServiceError):
    pass


class AccountBlockedError(AiChatServiceError):
    pass


class EmptyMessageError(AiChatServiceError):
    pass


class AiChatService:
    def __init__(
        self,
        *,
        chat_service: ChatService,
        user_service: UserService,
        file_repository: FileRepository,
        system_prompts: Mapping[str, str],
        models: Mapping[str, Mapping[str, Any]],
        default_template: str,
        default_model: str,
        gemini_api_key: str,
    ) -> None:
        self._chat_service = chat_service
        self._user_service = user_service
        self._file_repository = file_repository
        self._system_prompts = system_prompts
        self._models = models
        self._default_template = default_template
        self._default_model = default_model
        self._gemini_api_key = gemini_api_key

    async def send_message(
        self,
        *,
        chat_id: str,
        user_id: int,
        content: str,
        files_json: str,
    ) -> dict:
        if not self._gemini_api_key:
            raise GeminiApiKeyMissingError("GEMINI_API_KEY not set in .env")

        chat = await self._chat_service.get_chat(chat_id=chat_id, user_id=user_id)
        if not chat:
            raise ChatNotFoundError("Chat not found")

        user = await self._user_service.get_by_id(user_id)
        if user and user.get("is_blocked"):
            raise AccountBlockedError("Аккаунт заблокирован администратором")

        file_refs = json.loads(files_json)
        template_key = chat["template"] or self._default_template
        model_key = chat["model"] or self._default_model
        if model_key not in self._models:
            model_key = self._default_model
        system_prompt = self._system_prompts.get(template_key, self._system_prompts[self._default_template])

        history_rows = await self._chat_service.list_messages(chat_id=chat_id)
        gemini_history = [
            {
                "role": "user" if row["role"] == "user" else "model",
                "parts": [row["content"] or "…"],
            }
            for row in history_rows
        ]

        await self._chat_service.save_message(
            chat_id=chat_id,
            role="user",
            content=content,
            file_metas=[
                {"sha256": file_ref["sha256"], "original_filename": file_ref["original_filename"]}
                for file_ref in file_refs
            ],
        )

        parts = await self._build_turn_parts(content=content, file_refs=file_refs)
        if not parts:
            raise EmptyMessageError("Нет содержимого для отправки")

        course_id: str | None = chat.get("course_id")
        rag_images: list[dict] = []
        rag_result: dict = {}
        if file_refs:
            try:
                auto_course = await rag_engine.ensure_chat_course_and_ingest_uploads(
                    chat_id=chat_id,
                    user_id=user_id,
                    file_refs=file_refs,
                )
                if auto_course and not course_id:
                    course_id = auto_course
            except Exception as exc:
                print(f"[rag] auto-ingest failed: {exc}")

        if course_id:
            rag_result = await rag_engine.rag_query(
                query=content or " ".join(str(part) for part in parts if isinstance(part, str)),
                course_id=str(course_id),
                system_prompt=system_prompt,
                model_name=model_key,
                conversation_history=gemini_history,
            )
            assistant_text = rag_result["answer"]
            rag_images = rag_result["images"]
            cache_hit = bool(rag_result.get("from_cache"))
            api_tokens_override = rag_result.get("api_tokens", None)
            analytics.track(
                "rag_query",
                user_id,
                chat_id=str(chat_id),
                course_id=str(course_id),
                sources_found=rag_result["sources_found"],
                from_cache=rag_result["from_cache"],
            )
        else:
            assistant_text, api_tokens_raw = await self._send_standard_gemini(
                model_key=model_key,
                system_prompt=system_prompt,
                gemini_history=gemini_history,
                parts=parts,
                user_id=user_id,
            )
            api_tokens_override = None
            cache_hit = False

        if not course_id:
            api_tokens = api_tokens_raw  # type: ignore[name-defined]
        else:
            api_tokens = 3000 + max(1, len(assistant_text) // 4)
        if api_tokens_override is not None:
            api_tokens = api_tokens_override

        usage = self._calculate_usage(
            content=content,
            assistant_text=assistant_text,
            model_key=model_key,
            course_id=course_id,
            rag_result=rag_result,
            cache_hit=cache_hit,
        )

        assistant_message = await self._chat_service.save_message(
            chat_id=chat_id,
            role="assistant",
            content=assistant_text,
            tokens=usage["total_tokens"],
            model=model_key,
            cost_usd=usage["cost_usd"],
        )
        assistant_message = dict(assistant_message)
        if rag_images:
            assistant_message["rag_images"] = [
                {
                    "id": image["id"],
                    "caption": image["caption"],
                    "url": f"/api/rag/images/{image['id']}",
                    "mime": image["mime_type"],
                }
                for image in rag_images
            ]

        analytics.track(
            "message_sent",
            user_id,
            chat_id=str(chat_id),
            template=template_key,
            model=model_key,
            tokens=usage["total_tokens"],
            api_tokens=api_tokens,
            cost_usd=usage["cost_usd"],
            files=len(file_refs),
        )

        await self._chat_service.update_title_after_message(
            chat_id=chat_id,
            user_id=user_id,
            current_title=chat["title"],
            content=content,
        )

        billing_usage = {
            "model_name": model_key,
            "cache_hit": cache_hit,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "context_tokens": usage["context_tokens"],
            "total_tokens": usage["total_tokens"],
            "estimated_no_rag": usage["estimated_without_rag"],
            "actual_with_rag": usage["actual_with_rag"],
            "savings_pct": usage["savings_pct"],
            "cost_units": usage["cost_usd"],
            "status": "completed",
            "rag_metrics": {
                "query": content or "",
                "chunks_used": rag_result.get("chunks_used", 0) if course_id else 0,
                "context_tokens": usage["context_tokens"],
                "estimated_tokens_no_rag": usage["estimated_without_rag"],
                "latency_ms": rag_result.get("latency_ms", 0) if course_id else 0,
            } if course_id else None,
        }

        return {
            "assistant_message": assistant_message,
            "billing_usage": billing_usage,
        }

    async def _build_turn_parts(self, *, content: str, file_refs: list[dict]) -> list:
        parts: list = [content] if content else []
        for file_ref in file_refs:
            meta = await self._file_repository.get(file_ref["sha256"])
            if not meta:
                continue
            try:
                raw = storage.read_file(meta["sha256"], meta["compressed"])
                if meta["mime_type"].startswith("image/"):
                    parts.append(PIL.Image.open(io.BytesIO(raw)))
                else:
                    buffer = io.BytesIO(raw)
                    buffer.name = file_ref["original_filename"]
                    uploaded = genai.upload_file(buffer, mime_type=meta["mime_type"])
                    parts.append(uploaded)
            except Exception as exc:
                print(f"File attach error: {exc}")
        return parts

    async def _send_standard_gemini(
        self,
        *,
        model_key: str,
        system_prompt: str,
        gemini_history: list[dict],
        parts: list,
        user_id: int,
    ) -> tuple[str, int]:
        model = genai.GenerativeModel(
            model_name=self._model_name(model_key),
            system_instruction=system_prompt,
        )
        session = model.start_chat(history=gemini_history)
        loop = asyncio.get_event_loop()
        started = time.perf_counter()
        try:
            response = await loop.run_in_executor(None, lambda: session.send_message(parts))
            analytics.metrics.record_gemini(
                model_key,
                (time.perf_counter() - started) * 1000,
                ok=True,
            )
        except Exception as exc:
            analytics.metrics.record_gemini(
                model_key,
                (time.perf_counter() - started) * 1000,
                ok=False,
            )
            analytics.track("gemini_error", user_id, model=model_key, error=str(exc)[:200])
            raise

        assistant_text = response.text
        try:
            api_tokens_raw = response.usage_metadata.total_token_count
        except Exception:
            api_tokens_raw = max(1, len(assistant_text) // 4)
        return assistant_text, api_tokens_raw

    def _calculate_usage(
        self,
        *,
        content: str,
        assistant_text: str,
        model_key: str,
        course_id: str | None,
        rag_result: dict,
        cache_hit: bool,
    ) -> dict:
        input_tokens = max(1, len(content or "") // 4)
        context_tokens = rag_result.get("context_tokens", 0) if course_id else 0
        output_tokens = max(1, len(assistant_text or "") // 4)
        total_tokens = input_tokens + context_tokens + output_tokens
        estimated_without_rag = (
            rag_result.get("estimated_without_rag_tokens", total_tokens)
            if course_id else total_tokens
        )
        actual_with_rag = (
            rag_result.get("actual_with_rag_tokens", total_tokens)
            if course_id else total_tokens
        )
        savings_pct = 0.0
        if estimated_without_rag > 0:
            savings_pct = round(max((estimated_without_rag - actual_with_rag) / estimated_without_rag, 0) * 100, 3)
        cost_usd = calculate_cost_units(model_key, input_tokens, output_tokens, context_tokens)
        if cache_hit:
            cost_usd = round(cost_usd * 0.01, 8)

        return {
            "input_tokens": input_tokens,
            "context_tokens": context_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_without_rag": estimated_without_rag,
            "actual_with_rag": actual_with_rag,
            "savings_pct": savings_pct,
            "cost_usd": cost_usd,
        }

    @staticmethod
    def _model_name(key: str) -> str:
        if key.startswith("models/"):
            return key
        return f"models/{key}"
