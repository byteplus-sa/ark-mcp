"""Seed3D adapter — asynchronous 3D generation task management.

Translates domain input models to provider DTOs, calls the ModelArk gateway
for the four task operations (create, get, list, delete), and maps provider
responses to domain output models.

Hyper3D and Hitem3d share the same ``/contents/generations/tasks`` task
endpoints as Seedance, so this adapter mirrors ``SeedanceService`` while
using 3D-specific content items and the ``file_url`` output field.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.domain.models import (
    Seed3DTaskStatus,
    Seed3DTaskSummary,
    Seed3DTaskUsage,
)
from ark_mcp.observability.logger import debug as log_debug
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.providers.modelark.schemas import (
    Seed3DContentItem,
    Seed3DCreateProviderRequest,
    Seed3DCreateProviderResponse,
    Seed3DTaskListResponse,
    Seed3DTaskResponse,
)


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


def _truthy(value: Any) -> bool:
    """Treat empty strings, None, and empty containers as absent parameters."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return value is not None


class Seed3DService:
    """Service layer for Seed3D (Hyper3D + Hitem3d) generation tasks."""

    def __init__(self, gateway: ModelArkGateway | None = None) -> None:
        self._gateway = gateway or ModelArkGateway()

    async def create_task(self, request: Seed3DCreateProviderRequest) -> tuple[str, str | None]:
        """Call ``POST /contents/generations/tasks``.

        Returns ``(task_id, request_id)``.
        Raises ``NormalizedProviderError`` on failure.
        """
        log_debug("seed3d_create_task", model=request.model)
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
        parsed = Seed3DCreateProviderResponse.model_validate(body)
        log_debug(
            "seed3d_create_task_complete",
            task_id=parsed.id,
            status_code=response.status_code,
            request_id=request_id,
        )
        return parsed.id, request_id

    async def get_task(self, task_id: str) -> tuple[Seed3DTaskResponse, str | None]:
        """Call ``GET /contents/generations/tasks/{id}``.

        Returns ``(task, request_id)``.
        """
        log_debug("seed3d_get_task", task_id=task_id)
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
        parsed = Seed3DTaskResponse.model_validate(body)
        log_debug(
            "seed3d_get_task_complete",
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
    ) -> tuple[Seed3DTaskListResponse, str | None]:
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

        log_debug("seed3d_list_tasks", page=page, page_size=page_size)
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
        parsed = Seed3DTaskListResponse.model_validate(body)
        log_debug(
            "seed3d_list_tasks_complete",
            page=page,
            total=len(parsed.items) if parsed.items else 0,
            status_code=response.status_code,
            request_id=request_id,
        )
        return parsed, request_id

    async def delete_task(self, task_id: str) -> str | None:
        """Call ``DELETE /contents/generations/tasks/{id}``.

        Returns the request ID.
        """
        log_debug("seed3d_delete_task", task_id=task_id)
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
            "seed3d_delete_task_complete",
            task_id=task_id,
            status_code=response.status_code,
            request_id=request_id,
        )
        return request_id

    @staticmethod
    def build_text_command(params: dict[str, Any]) -> str:
        """Render model parameters as a ``--<param> <value>`` text command.

        Boolean values render as ``true``/``false``; lists render as
        comma-joined ``[a,b,c]``. Empty/None values are skipped so the
        provider applies its own defaults.
        """
        parts: list[str] = []
        for key, value in params.items():
            if not _truthy(value):
                continue
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, (list, tuple)):
                rendered = "[" + ",".join(str(v) for v in value) + "]"
            else:
                rendered = str(value)
            parts.append(f"--{key} {rendered}")
        return " ".join(parts)

    @staticmethod
    def build_content(
        *,
        prompt: str | None = None,
        command_params: dict[str, Any] | None = None,
        images: list[dict[str, Any]] | None = None,
    ) -> list[Seed3DContentItem]:
        """Build the provider ``content[]`` array from domain-level parameters.

        The first text item carries the prompt followed by any ``--k v``
        model commands. Image items follow as ``image_url`` entries.
        """
        content: list[Seed3DContentItem] = []

        text_parts: list[str] = []
        if prompt:
            text_parts.append(prompt)
        command = Seed3DService.build_text_command(command_params or {})
        if command:
            text_parts.append(command)
        if text_parts:
            content.append(Seed3DContentItem(type="text", text=" ".join(text_parts)))

        if images:
            for img in images:
                url_value: str
                if img.get("kind") == "base64" and img.get("data"):
                    mime = img.get("mime_type", "image/png")
                    url_value = f"data:{mime};base64,{img['data']}"
                else:
                    url_value = img.get("url", "")
                content.append(
                    Seed3DContentItem(
                        type="image_url",
                        image_url={"url": url_value},
                    )
                )

        return content

    @staticmethod
    def build_request(
        *,
        model: str,
        content: list[Seed3DContentItem],
        seed: int | None = None,
        callback_url: str | None = None,
    ) -> Seed3DCreateProviderRequest:
        """Build a provider request from domain-level parameters."""
        return Seed3DCreateProviderRequest(
            model=model,
            content=content,
            seed=seed,
            callback_url=callback_url,
        )

    @staticmethod
    def to_task_summary(task: Seed3DTaskResponse) -> Seed3DTaskSummary:
        """Convert a full task response to a summary for list results."""
        return Seed3DTaskSummary(
            task_id=task.id,
            model=task.model,
            status=Seed3DTaskStatus(task.status),
            created_at=str(task.created_at or ""),
            updated_at=str(task.updated_at or ""),
        )

    @staticmethod
    def extract_usage(task: Seed3DTaskResponse) -> Seed3DTaskUsage | None:
        """Extract usage data from a task response."""
        if task.usage is None:
            return None
        return Seed3DTaskUsage(
            completion_tokens=task.usage.completion_tokens,
            total_tokens=task.usage.total_tokens,
        )

    @staticmethod
    def get_created_at(task: Seed3DTaskResponse) -> str:
        """Format the task's ``created_at`` as ISO-8601."""
        raw = task.created_at
        if raw is None:
            return datetime.now(UTC).isoformat()
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=UTC).isoformat()
        return str(raw)

    @staticmethod
    def get_updated_at(task: Seed3DTaskResponse) -> str:
        """Format the task's ``updated_at`` as ISO-8601."""
        raw = task.updated_at
        if raw is None:
            return datetime.now(UTC).isoformat()
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=UTC).isoformat()
        return str(raw)

    async def close(self) -> None:
        await self._gateway.close()
