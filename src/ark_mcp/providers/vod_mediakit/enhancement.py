"""Enhancement-submission adapter for BytePlus VOD AI MediaKit."""

from __future__ import annotations

import json

import httpx
from pydantic import ValidationError

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.observability.logger import debug as log_debug
from ark_mcp.providers.vod_mediakit._task_utils import (
    normalize_timestamp,
    sanitize_task_error,
)
from ark_mcp.providers.vod_mediakit.client import (
    VodMediaKitGateway,
    sanitize_provider_message,
)
from ark_mcp.providers.vod_mediakit.schemas import (
    EnhancementSubmission,
    EnhancementTask,
    VodMediaKitAcceptedResponse,
    VodMediaKitEnhancementRequest,
    VodMediaKitEnhancementTaskResponse,
    VodMediaKitProviderResponse,
)

_ENHANCE_PATH = "/tools/enhance-video"
_TASKS_PATH = "/tasks"
_OPERATION_SUBMIT = "enhance_video"
_OPERATION_GET = "get_enhancement_task"


class VodMediaKitEnhancementService:
    """Submit the exact verified video-enhancement profile to MediaKit."""

    def __init__(self, gateway: VodMediaKitGateway | None = None) -> None:
        self._gateway = gateway or VodMediaKitGateway()

    async def enhance(self, request: VodMediaKitEnhancementRequest) -> EnhancementSubmission:
        """Submit one enhancement request without automatic mutation retries."""
        log_debug("vod_enhance_video_submit")
        try:
            response = await self._gateway.post(
                _ENHANCE_PATH,
                request.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
        except httpx.TimeoutException:
            raise VodMediaKitGateway.normalize_ambiguous_transport_error(
                _OPERATION_SUBMIT,
                code="TIMEOUT",
                message=(
                    "MediaKit enhancement timed out after dispatch and may have completed. "
                    "Do not retry blindly; reconcile with BytePlus support using request telemetry."
                ),
            ) from None
        except httpx.TransportError:
            raise VodMediaKitGateway.normalize_ambiguous_transport_error(
                _OPERATION_SUBMIT,
                code="TRANSPORT_ERROR",
                message=(
                    "MediaKit transport failed after dispatch and completion is unknown. "
                    "Do not retry blindly."
                ),
            ) from None

        header_request_id = VodMediaKitGateway.extract_request_id(response)
        if not 200 <= response.status_code < 300:
            raise VodMediaKitGateway.normalize_error(response, _OPERATION_SUBMIT)

        try:
            body = response.json()
            if (
                isinstance(body, dict)
                and "task_id" in body
                and not {
                    "data",
                    "result",
                }.intersection(body)
            ):
                accepted = VodMediaKitAcceptedResponse.model_validate(body)
                log_debug(
                    "vod_enhance_video_complete",
                    status="accepted",
                    status_code=response.status_code,
                    task_id=accepted.task_id,
                    request_id=header_request_id,
                )
                return EnhancementSubmission(
                    status="accepted",
                    request_id=accepted.request_id,
                    provider_log_id=header_request_id,
                    task_id=accepted.task_id,
                )
            provider_response = VodMediaKitProviderResponse.model_validate(body)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise ProviderError(
                NormalizedProviderError(
                    provider="byteplus-vod-mediakit",
                    operation=_OPERATION_SUBMIT,
                    http_status=response.status_code,
                    code="INVALID_RESPONSE",
                    message=(
                        "MediaKit returned an unsupported success response. "
                        "The provider contract must be verified before this response can be accepted."
                    ),
                    request_id=header_request_id,
                    retryable=False,
                    ambiguous_completion=False,
                )
            ) from exc

        result = provider_response.result
        detail = result.error
        log_debug(
            "vod_enhance_video_complete",
            status="succeeded",
            status_code=response.status_code,
            task_id=result.task_id,
            request_id=header_request_id,
        )
        return EnhancementSubmission(
            status="succeeded",
            request_id=provider_response.request_id or result.request_id,
            provider_log_id=header_request_id,
            task_id=result.task_id,
            output_url=result.output_url,
            mime_type=result.mime_type,
            expires_at=result.expires_at,
            output_size_bytes=result.output_size_bytes,
            provider_status=result.status,
            failure_code=detail.code if detail else None,
            failure_message=(
                sanitize_provider_message(detail.message, "MediaKit returned a provider warning.")
                if detail and detail.message
                else None
            ),
        )

    async def get(self, task_id: str) -> EnhancementTask:
        """Poll one enhancement task and normalize its state."""
        log_debug("vod_enhancement_get", task_id=task_id)
        try:
            response = await self._gateway.get(f"{_TASKS_PATH}/{task_id}")
        except httpx.TimeoutException:
            raise VodMediaKitGateway.normalize_ambiguous_transport_error(
                _OPERATION_GET,
                side_effect=False,
                code="TIMEOUT",
                message="MediaKit enhancement task poll timed out.",
            ) from None
        except httpx.TransportError:
            raise VodMediaKitGateway.normalize_ambiguous_transport_error(
                _OPERATION_GET,
                side_effect=False,
                code="TRANSPORT_ERROR",
                message="MediaKit enhancement task poll failed to connect.",
            ) from None

        header_request_id = VodMediaKitGateway.extract_request_id(response)
        if not 200 <= response.status_code < 300:
            raise VodMediaKitGateway.normalize_error(response, _OPERATION_GET)

        try:
            parsed = VodMediaKitEnhancementTaskResponse.model_validate(response.json())
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ProviderError(
                NormalizedProviderError(
                    provider="byteplus-vod-mediakit",
                    operation=_OPERATION_GET,
                    http_status=response.status_code,
                    code="INVALID_RESPONSE",
                    message="MediaKit returned an unsupported enhancement task response.",
                    request_id=header_request_id,
                    retryable=False,
                    ambiguous_completion=False,
                )
            ) from exc

        if parsed.task_id != task_id:
            raise ProviderError(
                NormalizedProviderError(
                    provider="byteplus-vod-mediakit",
                    operation=_OPERATION_GET,
                    http_status=response.status_code,
                    code="INVALID_RESPONSE",
                    message="MediaKit returned a mismatched enhancement task response.",
                    request_id=header_request_id,
                    retryable=False,
                    ambiguous_completion=False,
                )
            )
        result = parsed.result

        if parsed.status == "completed":
            if result is None:
                raise ProviderError(
                    NormalizedProviderError(
                        provider="byteplus-vod-mediakit",
                        operation=_OPERATION_GET,
                        http_status=response.status_code,
                        code="INVALID_RESPONSE",
                        message="MediaKit reported a completed enhancement task without an output URL.",
                        request_id=header_request_id,
                        retryable=False,
                        ambiguous_completion=False,
                    )
                )
            request_id = parsed.request_id or header_request_id
            return EnhancementTask(
                task_id=parsed.task_id,
                status="succeeded",
                provider_status=parsed.status,
                request_id=request_id,
                output_url=result.video_url,
                duration_seconds=result.duration,
                fps=result.fps,
                resolution=result.resolution,
                tool_version=result.tool_version,
                created_at=normalize_timestamp(parsed.created_at),
                finished_at=normalize_timestamp(parsed.finished_at),
                source_expires_at=normalize_timestamp(parsed.expires_at),
            )

        if parsed.status == "failed":
            request_id = parsed.request_id or header_request_id
            code, message = sanitize_task_error(
                parsed.error, "MediaKit reported the enhancement task failed."
            )
            return EnhancementTask(
                task_id=parsed.task_id,
                status="failed",
                provider_status=parsed.status,
                request_id=request_id,
                failure_code=code,
                failure_message=message,
                created_at=normalize_timestamp(parsed.created_at),
                finished_at=normalize_timestamp(parsed.finished_at),
            )

        if parsed.status == "running":
            request_id = parsed.request_id or header_request_id
            return EnhancementTask(
                task_id=parsed.task_id,
                status="processing",
                provider_status=parsed.status,
                request_id=request_id,
                created_at=normalize_timestamp(parsed.created_at),
            )

        raise ProviderError(
            NormalizedProviderError(
                provider="byteplus-vod-mediakit",
                operation=_OPERATION_GET,
                http_status=response.status_code,
                code="INVALID_RESPONSE",
                message="MediaKit returned an unrecognized enhancement task status.",
                request_id=header_request_id,
                retryable=False,
                ambiguous_completion=False,
            )
        )

    async def close(self) -> None:
        """Close the owned/shared HTTP gateway."""
        await self._gateway.close()
