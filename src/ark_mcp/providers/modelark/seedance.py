"""Seedance adapter — asynchronous video generation task management.

Translates domain input models to provider DTOs, calls the ModelArk gateway
for the four task operations (create, get, list, delete), and maps provider
responses to domain output models.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.domain.models import (
    SeedanceTaskStatus,
    SeedanceTaskSummary,
    SeedanceTaskUsage,
)
from ark_mcp.observability.logger import debug as log_debug
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.providers.modelark.schemas import (
    SeedanceContentItem,
    SeedanceCreateProviderRequest,
    SeedanceCreateProviderResponse,
    SeedanceTaskListResponse,
    SeedanceTaskResponse,
)
from ark_mcp.providers.modelark.seedance_queue import queue_info


def _parse_success_body(response: httpx.Response, operation: str) -> dict[str, Any]:
    """Parse a success-path JSON body, raising ``ProviderError`` on malformed JSON."""
    try:
        return cast("dict[str, Any]", response.json())
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderError(
            NormalizedProviderError(
                provider="modelark",
                operation=operation,
                http_status=response.status_code,
                code="INVALID_RESPONSE",
                message="ModelArk returned a non-JSON success response.",
                request_id=ModelArkGateway.extract_request_id(response),
                retryable=False,
                ambiguous_completion=False,
            )
        ) from exc


class SeedanceService:
    """Service layer for Seedance video generation tasks."""

    def __init__(self, gateway: ModelArkGateway | None = None) -> None:
        self._gateway = gateway or ModelArkGateway()

    async def create_task(self, request: SeedanceCreateProviderRequest) -> tuple[str, str | None]:
        """Call ``POST /contents/generations/tasks``.

        Returns ``(task_id, request_id)``.
        Raises ``NormalizedProviderError`` on failure.
        """
        log_debug("seedance_create_task", model=request.model)
        try:
            response = await self._gateway.post(
                "/contents/generations/tasks",
                request.model_dump(exclude_none=True),
            )
        except httpx.TimeoutException:
            raise ModelArkGateway.normalize_timeout("create_task") from None
        except httpx.ConnectError as exc:
            raise ModelArkGateway.normalize_connection_error("create_task", exc) from exc
        except httpx.TransportError as exc:
            raise ModelArkGateway.normalize_transport_error("create_task", exc) from exc

        request_id = ModelArkGateway.extract_request_id(response)

        if response.status_code >= 400:
            raise ModelArkGateway.normalize_error(response, "create_task")

        body = _parse_success_body(response, "create_task")
        parsed = SeedanceCreateProviderResponse.model_validate(body)
        log_debug(
            "seedance_create_task_complete",
            task_id=parsed.id,
            status_code=response.status_code,
            request_id=request_id,
        )
        return parsed.id, request_id

    async def get_task(self, task_id: str) -> tuple[SeedanceTaskResponse, str | None]:
        """Call ``GET /contents/generations/tasks/{id}``.

        Returns ``(task, request_id)``.
        """
        log_debug("seedance_get_task", task_id=task_id)
        try:
            response = await self._gateway.get(f"/contents/generations/tasks/{task_id}")
        except httpx.TimeoutException:
            raise ModelArkGateway.normalize_timeout("get_task", side_effect=False) from None
        except httpx.ConnectError as exc:
            raise ModelArkGateway.normalize_connection_error("get_task", exc) from exc
        except httpx.TransportError as exc:
            raise ModelArkGateway.normalize_transport_error("get_task", exc) from exc

        request_id = ModelArkGateway.extract_request_id(response)

        if response.status_code >= 400:
            raise ModelArkGateway.normalize_error(response, "get_task")

        body = _parse_success_body(response, "get_task")
        parsed = SeedanceTaskResponse.model_validate(body)
        log_debug(
            "seedance_get_task_complete",
            task_id=task_id,
            status=parsed.status,
            status_code=response.status_code,
            request_id=request_id,
        )
        return parsed, request_id

    async def list_tasks(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        task_ids: list[str] | None = None,
        model: str | None = None,
        service_tier: str | None = None,
    ) -> tuple[SeedanceTaskListResponse, str | None]:
        """Call ``GET /contents/generations/tasks``.

        Returns ``(page, request_id)``.
        """
        params: dict[str, Any] = {
            "page_num": page,
            "page_size": page_size,
        }
        if status:
            params["filter.status"] = status
        if task_ids:
            params["filter.task_ids"] = task_ids
        if model:
            params["filter.model"] = model
        if service_tier:
            params["filter.service_tier"] = service_tier

        log_debug("seedance_list_tasks", page=page, page_size=page_size)
        try:
            response = await self._gateway.get("/contents/generations/tasks", params=params)
        except httpx.TimeoutException:
            raise ModelArkGateway.normalize_timeout("list_tasks", side_effect=False) from None
        except httpx.ConnectError as exc:
            raise ModelArkGateway.normalize_connection_error("list_tasks", exc) from exc
        except httpx.TransportError as exc:
            raise ModelArkGateway.normalize_transport_error("list_tasks", exc) from exc

        request_id = ModelArkGateway.extract_request_id(response)

        if response.status_code >= 400:
            raise ModelArkGateway.normalize_error(response, "list_tasks")

        body = _parse_success_body(response, "list_tasks")
        parsed = SeedanceTaskListResponse.model_validate(body)
        log_debug(
            "seedance_list_tasks_complete",
            page=page,
            total=len(parsed.data) if parsed.data else 0,
            status_code=response.status_code,
            request_id=request_id,
        )
        return parsed, request_id

    async def delete_task(self, task_id: str) -> str | None:
        """Call ``DELETE /contents/generations/tasks/{id}``.

        Returns the request ID.
        """
        log_debug("seedance_delete_task", task_id=task_id)
        try:
            response = await self._gateway.delete(f"/contents/generations/tasks/{task_id}")
        except httpx.TimeoutException:
            raise ModelArkGateway.normalize_timeout("delete_task") from None
        except httpx.ConnectError as exc:
            raise ModelArkGateway.normalize_connection_error("delete_task", exc) from exc
        except httpx.TransportError as exc:
            raise ModelArkGateway.normalize_transport_error("delete_task", exc) from exc

        request_id = ModelArkGateway.extract_request_id(response)

        if response.status_code >= 400:
            raise ModelArkGateway.normalize_error(response, "delete_task")

        log_debug(
            "seedance_delete_task_complete",
            task_id=task_id,
            status_code=response.status_code,
            request_id=request_id,
        )
        return request_id

    @staticmethod
    def build_content(
        *,
        prompt: str | None = None,
        images: list[dict[str, Any]] | None = None,
        videos: list[dict[str, Any]] | None = None,
        audios: list[dict[str, Any]] | None = None,
    ) -> list[SeedanceContentItem]:
        """Build the provider ``content[]`` array from domain-level parameters."""
        content: list[SeedanceContentItem] = []

        if prompt:
            content.append(SeedanceContentItem(type="text", text=prompt))

        if images:
            for img in images:
                url_value: str
                if img.get("kind") == "base64" and img.get("data"):
                    mime = img.get("mime_type", "image/png")
                    url_value = f"data:{mime};base64,{img['data']}"
                else:
                    url_value = img.get("url", "")

                content.append(
                    SeedanceContentItem(
                        type="image_url",
                        image_url={"url": url_value},
                        role=img.get("role"),
                    )
                )

        if videos:
            for vid in videos:
                content.append(
                    SeedanceContentItem(
                        type="video_url",
                        video_url={"url": vid.get("url", "")},
                        role=vid.get("role", "reference_video"),
                    )
                )

        if audios:
            for aud in audios:
                audio_url_value: str
                if aud.get("kind") == "base64" and aud.get("data"):
                    mime = aud.get("mime_type", "audio/mpeg")
                    audio_url_value = f"data:{mime};base64,{aud['data']}"
                else:
                    audio_url_value = aud.get("url", "")

                content.append(
                    SeedanceContentItem(
                        type="audio_url",
                        audio_url={"url": audio_url_value},
                        role=aud.get("role", "reference_audio"),
                    )
                )

        return content

    @staticmethod
    def build_request(
        *,
        model: str,
        content: list[SeedanceContentItem],
        resolution: str | None = None,
        ratio: str | None = None,
        duration: int | None = None,
        generate_audio: bool | None = None,
        watermark: bool | None = None,
        return_last_frame: bool | None = None,
        execution_expires_after: int | None = None,
        priority: int | None = None,
        safety_identifier: str | None = None,
        omni_reference_task_type: str | None = None,
    ) -> SeedanceCreateProviderRequest:
        """Build a provider request from domain-level parameters."""
        return SeedanceCreateProviderRequest(
            model=model,
            content=content,
            resolution=resolution,
            ratio=ratio,
            duration=duration,
            generate_audio=generate_audio,
            watermark=watermark,
            return_last_frame=return_last_frame,
            execution_expires_after=execution_expires_after,
            priority=priority,
            safety_identifier=safety_identifier,
            omni_reference_task_type=omni_reference_task_type,
        )

    @staticmethod
    def to_task_summary(task: SeedanceTaskResponse) -> SeedanceTaskSummary:
        """Convert a full task response to a summary for list results."""
        return SeedanceTaskSummary(
            task_id=task.id,
            model=task.model,
            status=SeedanceTaskStatus(task.status),
            created_at=str(task.created_at or ""),
            updated_at=str(task.updated_at or ""),
            queue=queue_info(task),
        )

    @staticmethod
    def extract_usage(task: SeedanceTaskResponse) -> SeedanceTaskUsage | None:
        """Extract usage data from a task response."""
        if task.usage is None:
            return None
        return SeedanceTaskUsage(
            completion_tokens=task.usage.completion_tokens,
            prompt_tokens=task.usage.prompt_tokens,
        )

    @staticmethod
    def get_created_at(task: SeedanceTaskResponse) -> str:
        """Format the task's ``created_at`` as ISO-8601."""
        raw = task.created_at
        if raw is None:
            return datetime.now(UTC).isoformat()
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=UTC).isoformat()
        return str(raw)

    @staticmethod
    def get_updated_at(task: SeedanceTaskResponse) -> str:
        """Format the task's ``updated_at`` as ISO-8601."""
        raw = task.updated_at
        if raw is None:
            return datetime.now(UTC).isoformat()
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=UTC).isoformat()
        return str(raw)

    async def close(self) -> None:
        await self._gateway.close()
