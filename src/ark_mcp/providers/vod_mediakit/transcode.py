"""Transcode-submission and task-polling adapter for BytePlus VOD AI MediaKit."""

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
from ark_mcp.providers.vod_mediakit.client import VodMediaKitGateway
from ark_mcp.providers.vod_mediakit.schemas import (
    TranscodeSubmission,
    TranscodeTask,
    VodMediaKitAcceptedResponse,
    VodMediaKitTranscodeRequest,
    VodMediaKitTranscodeTaskResponse,
)

_TRANSCODE_PATH = "/tools/transcode-video"
_TASKS_PATH = "/tasks"
_OPERATION_SUBMIT = "transcode_video"
_OPERATION_GET = "get_transcode_task"


class VodMediaKitTranscodeService:
    """Submit transcode tasks and poll their status on the MediaKit surface."""

    def __init__(self, gateway: VodMediaKitGateway | None = None) -> None:
        self._gateway = gateway or VodMediaKitGateway()

    async def submit(self, request: VodMediaKitTranscodeRequest) -> TranscodeSubmission:
        """Submit one transcode task; never retries an ambiguous mutation."""
        log_debug("vod_transcode_submit")
        try:
            response = await self._gateway.post(
                _TRANSCODE_PATH,
                request.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
        except httpx.TimeoutException:
            raise VodMediaKitGateway.normalize_ambiguous_transport_error(
                _OPERATION_SUBMIT,
                code="TIMEOUT",
                message=(
                    "MediaKit transcode submission timed out after dispatch and may have completed. "
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
        except json.JSONDecodeError as exc:
            raise ProviderError(
                NormalizedProviderError(
                    provider="byteplus-vod-mediakit",
                    operation=_OPERATION_SUBMIT,
                    http_status=response.status_code,
                    code="INVALID_RESPONSE",
                    message="MediaKit returned a non-JSON success response.",
                    request_id=header_request_id,
                    retryable=False,
                    ambiguous_completion=False,
                )
            ) from exc

        try:
            accepted = VodMediaKitAcceptedResponse.model_validate(body)
        except ValidationError as exc:
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

        return TranscodeSubmission(
            status="accepted",
            request_id=accepted.request_id,
            provider_log_id=header_request_id,
            task_id=accepted.task_id,
        )

    async def get(self, task_id: str) -> TranscodeTask:
        """Poll one transcode task and normalize its state."""
        log_debug("vod_transcode_get", task_id=task_id)
        try:
            response = await self._gateway.get(f"{_TASKS_PATH}/{task_id}")
        except httpx.TimeoutException:
            raise VodMediaKitGateway.normalize_ambiguous_transport_error(
                _OPERATION_GET,
                side_effect=False,
                code="TIMEOUT",
                message="MediaKit transcode task poll timed out.",
            ) from None
        except httpx.TransportError:
            raise VodMediaKitGateway.normalize_ambiguous_transport_error(
                _OPERATION_GET,
                side_effect=False,
                code="TRANSPORT_ERROR",
                message="MediaKit transcode task poll failed to connect.",
            ) from None

        header_request_id = VodMediaKitGateway.extract_request_id(response)
        if not 200 <= response.status_code < 300:
            raise VodMediaKitGateway.normalize_error(response, _OPERATION_GET)

        try:
            body = response.json()
            parsed = VodMediaKitTranscodeTaskResponse.model_validate(body)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ProviderError(
                NormalizedProviderError(
                    provider="byteplus-vod-mediakit",
                    operation=_OPERATION_GET,
                    http_status=response.status_code,
                    code="INVALID_RESPONSE",
                    message="MediaKit returned an unsupported task response.",
                    request_id=header_request_id,
                    retryable=False,
                    ambiguous_completion=False,
                )
            ) from exc

        request_id = parsed.request_id or header_request_id
        task_id = parsed.task_id
        result = parsed.result

        if parsed.status == "completed":
            if result is None or result.video_url is None:
                raise ProviderError(
                    NormalizedProviderError(
                        provider="byteplus-vod-mediakit",
                        operation=_OPERATION_GET,
                        http_status=response.status_code,
                        code="INVALID_RESPONSE",
                        message="MediaKit reported a completed task without an output URL.",
                        request_id=request_id,
                        retryable=False,
                        ambiguous_completion=False,
                    )
                )
            log_debug(
                "vod_transcode_get_complete",
                task_id=task_id,
                status="succeeded",
                status_code=response.status_code,
                request_id=request_id,
            )
            return TranscodeTask(
                task_id=task_id,
                status="succeeded",
                provider_status=parsed.status,
                request_id=request_id,
                output_url=result.video_url,
                duration_seconds=result.duration,
                resolution=result.resolution,
                video_codec=result.video_codec,
                created_at=normalize_timestamp(parsed.created_at),
                finished_at=normalize_timestamp(parsed.finished_at),
                source_expires_at=normalize_timestamp(parsed.expires_at),
            )

        if parsed.status == "failed":
            code, message = sanitize_task_error(
                parsed.error, "MediaKit reported the transcode task failed."
            )
            log_debug(
                "vod_transcode_get_complete",
                task_id=task_id,
                status="failed",
                status_code=response.status_code,
                request_id=request_id,
                failure_code=code,
            )
            return TranscodeTask(
                task_id=task_id,
                status="failed",
                provider_status=parsed.status,
                request_id=request_id,
                failure_code=code,
                failure_message=message,
                created_at=normalize_timestamp(parsed.created_at),
                finished_at=normalize_timestamp(parsed.finished_at),
            )

        if parsed.status == "running":
            log_debug(
                "vod_transcode_get_complete",
                task_id=task_id,
                status="processing",
                status_code=response.status_code,
                request_id=request_id,
            )
            return TranscodeTask(
                task_id=task_id,
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
                message=(
                    f"MediaKit returned an unrecognized transcode status '{parsed.status}'. "
                    "The status contract must be verified before it can be accepted."
                ),
                request_id=request_id,
                retryable=False,
                ambiguous_completion=False,
            )
        )

    async def close(self) -> None:
        """Close the owned/shared HTTP gateway."""
        await self._gateway.close()
