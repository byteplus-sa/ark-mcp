"""Subtitle burn-in and precision-erasure adapters for VOD AI MediaKit."""

from __future__ import annotations

import json
import re

import httpx
from pydantic import BaseModel, ValidationError

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.observability.logger import debug as log_debug
from ark_mcp.providers.vod_mediakit._task_utils import (
    normalize_timestamp,
    sanitize_task_error,
)
from ark_mcp.providers.vod_mediakit.client import VodMediaKitGateway
from ark_mcp.providers.vod_mediakit.schemas import (
    SubtitleSubmission,
    SubtitleTask,
    VodMediaKitAcceptedResponse,
    VodMediaKitAddSubtitlesRequest,
    VodMediaKitRemoveSubtitlesRequest,
    VodMediaKitSubtitleTaskResponse,
)

_ADD_PATH = "/tools/add-subtitle-to-video"
_REMOVE_PATH = "/tools/erase-video-subtitle-pro"
_TASKS_PATH = "/tasks"
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")


def _provider_error(
    *,
    operation: str,
    code: str,
    message: str,
    request_id: str | None,
    http_status: int | None,
    ambiguous_completion: bool = False,
    retryable: bool = False,
) -> ProviderError:
    return ProviderError(
        NormalizedProviderError(
            provider="byteplus-vod-mediakit",
            operation=operation,
            http_status=http_status,
            code=code,
            message=message,
            request_id=request_id,
            retryable=retryable,
            ambiguous_completion=ambiguous_completion,
        )
    )


async def _submit[RequestT: BaseModel](
    gateway: VodMediaKitGateway,
    request: RequestT,
    *,
    path: str,
    operation: str,
    label: str,
) -> SubtitleSubmission:
    log_debug(f"vod_{operation}_submit")
    try:
        response = await gateway.post(
            path,
            request.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
    except httpx.TimeoutException:
        raise VodMediaKitGateway.normalize_ambiguous_transport_error(
            operation,
            code="TIMEOUT",
            message=(
                f"MediaKit {label} submission timed out after dispatch and may have completed. "
                "Do not retry blindly; reuse the same client_token when reconciling the request."
            ),
        ) from None
    except httpx.TransportError:
        raise VodMediaKitGateway.normalize_ambiguous_transport_error(
            operation,
            code="TRANSPORT_ERROR",
            message=(
                f"MediaKit {label} transport failed after dispatch and completion is unknown. "
                "Do not retry blindly; reuse the same client_token when reconciling the request."
            ),
        ) from None

    header_request_id = VodMediaKitGateway.extract_request_id(response)
    if not 200 <= response.status_code < 300:
        raise VodMediaKitGateway.normalize_error(response, operation)

    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise _provider_error(
            operation=operation,
            code="INVALID_RESPONSE",
            message=(
                f"MediaKit returned an unsupported {label} success response after dispatch. "
                "Completion is unknown; do not retry blindly."
            ),
            request_id=header_request_id,
            http_status=response.status_code,
            ambiguous_completion=True,
        ) from exc

    if isinstance(body, dict) and body.get("success") is False:
        raise VodMediaKitGateway.normalize_error(response, operation)

    try:
        accepted = VodMediaKitAcceptedResponse.model_validate(body)
    except ValidationError as exc:
        raise _provider_error(
            operation=operation,
            code="INVALID_RESPONSE",
            message=(
                f"MediaKit returned an unsupported {label} success response after dispatch. "
                "Completion is unknown; do not retry blindly."
            ),
            request_id=header_request_id,
            http_status=response.status_code,
            ambiguous_completion=True,
        ) from exc

    return SubtitleSubmission(
        status="accepted",
        request_id=accepted.request_id,
        provider_log_id=header_request_id,
        task_id=accepted.task_id,
    )


async def _get(
    gateway: VodMediaKitGateway,
    task_id: str,
    *,
    operation: str,
    task_type: str,
    label: str,
) -> SubtitleTask:
    if not _TASK_ID_PATTERN.fullmatch(task_id):
        raise _provider_error(
            operation=operation,
            code="INVALID_TASK_ID",
            message="MediaKit task ID is invalid.",
            request_id=None,
            http_status=None,
        )

    log_debug(f"vod_{operation}", task_id=task_id)
    try:
        response = await gateway.get(f"{_TASKS_PATH}/{task_id}")
    except httpx.TimeoutException:
        raise _provider_error(
            operation=operation,
            code="TIMEOUT",
            message=f"MediaKit {label} task poll timed out.",
            request_id=None,
            http_status=None,
            retryable=True,
        ) from None
    except httpx.TransportError:
        raise _provider_error(
            operation=operation,
            code="TRANSPORT_ERROR",
            message=f"MediaKit {label} task poll failed to connect.",
            request_id=None,
            http_status=None,
            retryable=True,
        ) from None

    header_request_id = VodMediaKitGateway.extract_request_id(response)
    if not 200 <= response.status_code < 300:
        raise VodMediaKitGateway.normalize_error(response, operation)

    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise _provider_error(
            operation=operation,
            code="INVALID_RESPONSE",
            message=f"MediaKit returned an unsupported {label} task response.",
            request_id=header_request_id,
            http_status=response.status_code,
        ) from exc

    if isinstance(body, dict) and body.get("success") is False:
        raise VodMediaKitGateway.normalize_error(response, operation)

    try:
        parsed = VodMediaKitSubtitleTaskResponse.model_validate(body)
    except ValidationError as exc:
        raise _provider_error(
            operation=operation,
            code="INVALID_RESPONSE",
            message=f"MediaKit returned an unsupported {label} task response.",
            request_id=header_request_id,
            http_status=response.status_code,
        ) from exc

    request_id = parsed.request_id or header_request_id
    if parsed.task_id != task_id or parsed.task_type != task_type:
        raise _provider_error(
            operation=operation,
            code="INVALID_RESPONSE",
            message=f"MediaKit returned a mismatched {label} task response.",
            request_id=request_id,
            http_status=response.status_code,
        )

    if parsed.status == "completed":
        if parsed.result is None:
            raise _provider_error(
                operation=operation,
                code="INVALID_RESPONSE",
                message=f"MediaKit reported a completed {label} task without an output URL.",
                request_id=request_id,
                http_status=response.status_code,
            )
        return SubtitleTask(
            task_id=parsed.task_id,
            status="succeeded",
            provider_status=parsed.status,
            request_id=request_id,
            output_url=parsed.result.video_url,
            duration_seconds=parsed.result.duration,
            resolution=parsed.result.resolution,
            created_at=normalize_timestamp(parsed.created_at),
            finished_at=normalize_timestamp(parsed.finished_at),
            source_expires_at=normalize_timestamp(parsed.expires_at),
        )

    if parsed.status == "failed":
        code, message = sanitize_task_error(
            parsed.error,
            f"MediaKit reported the {label} task failed.",
        )
        return SubtitleTask(
            task_id=parsed.task_id,
            status="failed",
            provider_status=parsed.status,
            request_id=request_id,
            failure_code=code,
            failure_message=message,
            created_at=normalize_timestamp(parsed.created_at),
            finished_at=normalize_timestamp(parsed.finished_at),
        )

    if parsed.status in {"running", "processing"}:
        return SubtitleTask(
            task_id=parsed.task_id,
            status="processing",
            provider_status=parsed.status,
            request_id=request_id,
            created_at=normalize_timestamp(parsed.created_at),
        )

    raise _provider_error(
        operation=operation,
        code="INVALID_RESPONSE",
        message=f"MediaKit returned an unrecognized {label} task status.",
        request_id=request_id,
        http_status=response.status_code,
    )


class VodMediaKitSubtitleBurnInService:
    """Submit subtitle burn-in tasks and poll their MediaKit status."""

    def __init__(self, gateway: VodMediaKitGateway | None = None) -> None:
        self._gateway = gateway or VodMediaKitGateway()

    async def submit(self, request: VodMediaKitAddSubtitlesRequest) -> SubtitleSubmission:
        """Submit one subtitle burn-in task without automatic mutation retries."""
        return await _submit(
            self._gateway,
            request,
            path=_ADD_PATH,
            operation="add_subtitles",
            label="subtitle burn-in",
        )

    async def get(self, task_id: str) -> SubtitleTask:
        """Poll one subtitle burn-in task and normalize its state."""
        return await _get(
            self._gateway,
            task_id,
            operation="get_subtitle_addition_task",
            task_type="add-subtitle-to-video",
            label="subtitle burn-in",
        )

    async def close(self) -> None:
        """Close the owned or shared HTTP gateway."""
        await self._gateway.close()


class VodMediaKitSubtitleRemovalService:
    """Submit precision-erasure tasks and poll their MediaKit status."""

    def __init__(self, gateway: VodMediaKitGateway | None = None) -> None:
        self._gateway = gateway or VodMediaKitGateway()

    async def submit(self, request: VodMediaKitRemoveSubtitlesRequest) -> SubtitleSubmission:
        """Submit one precision-erasure task without automatic mutation retries."""
        return await _submit(
            self._gateway,
            request,
            path=_REMOVE_PATH,
            operation="remove_subtitles",
            label="subtitle removal",
        )

    async def get(self, task_id: str) -> SubtitleTask:
        """Poll one precision-erasure task and normalize its state."""
        return await _get(
            self._gateway,
            task_id,
            operation="get_subtitle_removal_task",
            task_type="erase-video-subtitle-pro",
            label="subtitle removal",
        )

    async def close(self) -> None:
        """Close the owned or shared HTTP gateway."""
        await self._gateway.close()
