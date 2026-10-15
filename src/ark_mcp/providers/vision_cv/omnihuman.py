"""OmniHuman 2.0 adapter: submit, query, and cancel image-plus-audio video tasks."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import ValidationError

from ark_mcp.config.env import get_settings
from ark_mcp.observability.logger import debug as log_debug
from ark_mcp.providers.vision_cv.client import VisionCvGateway, clean_provider_message
from ark_mcp.providers.vision_cv.schemas import (
    CANCEL_NOT_ALLOWED_CODE,
    PROVIDER_STATUS_MAP,
    SUCCESS_CODE,
    CvEnvelope,
    OmniHumanCancelResult,
    OmniHumanSubmission,
    OmniHumanSubmitRequest,
    OmniHumanTaskResult,
)

_SUBMIT_PATH = "/api/common/v3/submit_task_v2"
_GET_PATH = "/api/common/v3/get_result_v2"
_CANCEL_PATH = "/api/common/v3/cancel_task"
_OPERATION_SUBMIT = "omnihuman_create_task"
_OPERATION_GET = "omnihuman_get_task"
_OPERATION_CANCEL = "omnihuman_cancel_task"
_VIDEO_URL_TTL = timedelta(hours=1)


class OmniHumanService:
    """Service layer for OmniHuman 2.0 tasks on the Vision AI common v3 API."""

    def __init__(self, gateway: VisionCvGateway | None = None, req_key: str | None = None) -> None:
        self._gateway = gateway or VisionCvGateway()
        self._req_key = req_key or get_settings().omnihuman_req_key

    async def create(self, request: OmniHumanSubmitRequest) -> OmniHumanSubmission:
        """Submit one task without automatic retries; completion can be ambiguous."""
        log_debug("omnihuman_submit")
        response, envelope = await self._call(
            _SUBMIT_PATH, request.to_payload(self._req_key), _OPERATION_SUBMIT, mutation=True
        )
        self._require_success(response, envelope, _OPERATION_SUBMIT, mutation=True)
        task_id = (envelope.data or {}).get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise VisionCvGateway.invalid_response(
                _OPERATION_SUBMIT,
                http_status=response.status_code,
                request_id=self._request_id(response, envelope),
                mutation=True,
            )
        return OmniHumanSubmission(task_id=task_id, request_id=self._request_id(response, envelope))

    async def get(self, task_id: str) -> OmniHumanTaskResult:
        """Query one task and normalize its status."""
        log_debug("omnihuman_get", task_id=task_id)
        response, envelope = await self._call(
            _GET_PATH,
            {"req_key": self._req_key, "task_id": task_id},
            _OPERATION_GET,
            mutation=False,
        )
        request_id = self._request_id(response, envelope)
        data = envelope.data or {}
        provider_status = data.get("status")
        if envelope.code != SUCCESS_CODE:
            if provider_status == "done":
                return OmniHumanTaskResult(
                    status="failed",
                    request_id=request_id,
                    error_code=str(envelope.code),
                    error_message=clean_provider_message(
                        envelope.message, "OmniHuman generation failed."
                    ),
                )
            self._require_success(response, envelope, _OPERATION_GET, mutation=False)

        status = (
            PROVIDER_STATUS_MAP.get(provider_status) if isinstance(provider_status, str) else None
        )
        if status is None:
            raise VisionCvGateway.invalid_response(
                _OPERATION_GET,
                http_status=response.status_code,
                request_id=request_id,
                mutation=False,
            )
        if status != "succeeded":
            return OmniHumanTaskResult(status=status, request_id=request_id)
        video_url = data.get("video_url")
        if not isinstance(video_url, str) or not video_url:
            raise VisionCvGateway.invalid_response(
                _OPERATION_GET,
                http_status=response.status_code,
                request_id=request_id,
                mutation=False,
            )
        return OmniHumanTaskResult(
            status="succeeded",
            request_id=request_id,
            video_url=video_url,
            video_url_expires_at=datetime.now(UTC) + _VIDEO_URL_TTL,
        )

    async def cancel(self, task_id: str) -> OmniHumanCancelResult:
        """Cancel a queued task; a task already executing yields ``canceled=False``."""
        log_debug("omnihuman_cancel", task_id=task_id)
        response, envelope = await self._call(
            _CANCEL_PATH,
            {"req_key": self._req_key, "task_id": task_id},
            _OPERATION_CANCEL,
            mutation=True,
        )
        request_id = self._request_id(response, envelope)
        if envelope.code == CANCEL_NOT_ALLOWED_CODE:
            return OmniHumanCancelResult(
                canceled=False,
                request_id=request_id,
                message=(
                    "Task cannot be canceled because it already started or finished; "
                    "only tasks in the queue can be canceled."
                ),
            )
        self._require_success(response, envelope, _OPERATION_CANCEL, mutation=True)
        return OmniHumanCancelResult(canceled=True, request_id=request_id)

    async def close(self) -> None:
        await self._gateway.close()

    async def _call(
        self, path: str, body: dict[str, object], operation: str, *, mutation: bool
    ) -> tuple[httpx.Response, CvEnvelope]:
        try:
            response = await self._gateway.post(path, body)
        except httpx.TimeoutException:
            raise VisionCvGateway.normalize_timeout(operation, side_effect=mutation) from None
        except httpx.ConnectError as exc:
            raise VisionCvGateway.normalize_connection_error(operation, exc) from exc
        except httpx.TransportError as exc:
            if mutation:
                raise VisionCvGateway.ambiguous_transport_error(operation, exc) from exc
            raise VisionCvGateway.normalize_transport_error(operation, exc) from exc

        envelope = self._parse_envelope(response)
        if response.status_code >= 400:
            raise VisionCvGateway.error_from_envelope(
                envelope,
                operation,
                http_status=response.status_code,
                fallback_request_id=VisionCvGateway.extract_request_id(response),
                mutation=mutation,
                retry_after=response.headers.get("Retry-After"),
            )
        if envelope is None:
            raise VisionCvGateway.invalid_response(
                operation,
                http_status=response.status_code,
                request_id=VisionCvGateway.extract_request_id(response),
                mutation=mutation,
            )
        return response, envelope

    @staticmethod
    def _parse_envelope(response: httpx.Response) -> CvEnvelope | None:
        try:
            return CvEnvelope.model_validate(response.json())
        except (json.JSONDecodeError, ValueError, ValidationError):
            return None

    @staticmethod
    def _require_success(
        response: httpx.Response, envelope: CvEnvelope, operation: str, *, mutation: bool
    ) -> None:
        if envelope.code == SUCCESS_CODE:
            return
        raise VisionCvGateway.error_from_envelope(
            envelope,
            operation,
            http_status=response.status_code,
            fallback_request_id=VisionCvGateway.extract_request_id(response),
            mutation=mutation,
        )

    @staticmethod
    def _request_id(response: httpx.Response, envelope: CvEnvelope) -> str | None:
        return envelope.request_id or VisionCvGateway.extract_request_id(response)
