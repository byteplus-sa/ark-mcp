"""Seedance adapter — asynchronous video generation task management.

Translates domain input models to provider DTOs and creates Seedance tasks.
Retrieval, listing and deletion are shared with 3D tasks in
``generation_tasks.GenerationTaskService``.
"""

from __future__ import annotations

import json
from typing import Any, cast

import httpx

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.observability.logger import debug as log_debug
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.providers.modelark.schemas import (
    SeedanceContentItem,
    SeedanceCreateProviderRequest,
    SeedanceCreateProviderResponse,
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
    def build_draft_content(draft_task_id: str) -> list[SeedanceContentItem]:
        """Build the ``content[]`` array that renders a final video from a Draft task.

        The provider reuses the Draft task's prompt and media, so the content
        holds only the ``draft_task`` reference.
        """
        return [SeedanceContentItem(type="draft_task", draft_task={"id": draft_task_id})]

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
        draft: bool | None = None,
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
            draft=draft,
        )

    async def close(self) -> None:
        await self._gateway.close()
