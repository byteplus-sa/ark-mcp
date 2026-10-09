"""Contract tests for the provisional BytePlus VOD AI MediaKit adapter.

All responses are sanitized fixtures. No real provider request is made.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import ValidationError

from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.vod_mediakit.client import VodMediaKitGateway
from ark_mcp.providers.vod_mediakit.enhancement import VodMediaKitEnhancementService
from ark_mcp.providers.vod_mediakit.schemas import VodMediaKitEnhancementRequest

BASE_URL = "https://mediakit.ap-southeast-1.bytepluses.com/api/v1"
ENDPOINT = f"{BASE_URL}/tools/enhance-video"
FAST_ENDPOINT = f"{BASE_URL}/tools/enhance-video-fast"
TASK_ENDPOINT = f"{BASE_URL}/tasks/amk-tool-enhance-video-1"


@pytest.fixture
def service() -> VodMediaKitEnhancementService:
    """Create an isolated service with placeholder credentials."""
    return VodMediaKitEnhancementService(
        gateway=VodMediaKitGateway(
            api_key="test-mediakit-key",  # pragma: allowlist secret
            base_url=BASE_URL,
            timeout=10.0,
            connect_timeout=5.0,
        )
    )


def request() -> VodMediaKitEnhancementRequest:
    """Return the exact initial enhancement profile."""
    return VodMediaKitEnhancementRequest(
        video_url="https://media.example.com/source.mp4",
    )


class TestVodMediaKitRequestContract:
    """Verify the exact outbound mutation contract."""

    @respx.mock
    async def test_exact_path_headers_and_json(
        self,
        service: VodMediaKitEnhancementService,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        route = respx.post(ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {"output_url": "https://output.example.com/enhanced.mp4"},
                },
            )
        )

        await service.enhance(request())

        sent = route.calls.last.request
        assert sent.headers["Authorization"] == "Bearer test-mediakit-key"
        assert sent.headers["Content-Type"] == "application/json"
        assert sent.headers["Accept"] == "application/json"
        assert json.loads(sent.content) == {
            "video_url": "https://media.example.com/source.mp4",
            "scene": "common",
            "tool_version": "professional",
            "resolution": "4k",
            "bitrate_level": "high",
            "fps": 24,
            "Project": "default",
        }
        assert "test-mediakit-key" not in capsys.readouterr().err

    @respx.mock
    async def test_standard_request_sends_selected_resolution_and_style(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        route = respx.post(ENDPOINT).mock(
            return_value=httpx.Response(200, json={"success": True, "task_id": "t-1"})
        )

        await service.enhance(
            VodMediaKitEnhancementRequest(
                video_url="https://media.example.com/source.mp4",
                tool_version="standard",
                scene="aigc",
                enhance_style="natural",
                resolution="1080p",
                bitrate=8000,
                fps=None,
            )
        )

        assert json.loads(route.calls.last.request.content) == {
            "video_url": "https://media.example.com/source.mp4",
            "scene": "aigc",
            "tool_version": "standard",
            "enhance_style": "natural",
            "resolution": "1080p",
            "bitrate_level": "high",
            "bitrate": 8000,
            "Project": "default",
        }

    @respx.mock
    async def test_resolution_limit_replaces_the_default_resolution(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        route = respx.post(ENDPOINT).mock(
            return_value=httpx.Response(200, json={"success": True, "task_id": "t-1"})
        )

        await service.enhance(
            VodMediaKitEnhancementRequest(
                video_url="https://media.example.com/source.mp4", resolution_limit=1440
            )
        )

        body = json.loads(route.calls.last.request.content)
        assert body["resolution_limit"] == 1440
        assert "resolution" not in body

    @respx.mock
    async def test_fast_request_uses_fast_endpoint_without_unsupported_fields(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        standard_route = respx.post(ENDPOINT).mock(return_value=httpx.Response(500))
        fast_route = respx.post(FAST_ENDPOINT).mock(
            return_value=httpx.Response(
                200, json={"success": True, "task_id": "amk-tool-enhance-video-fast-1"}
            )
        )

        result = await service.enhance(
            VodMediaKitEnhancementRequest(
                video_url="https://media.example.com/source.mp4",
                tool_version="fast",
                resolution="720p",
                bitrate_level="medium",
                fps=30,
            )
        )

        assert result.status == "accepted"
        assert result.task_id == "amk-tool-enhance-video-fast-1"
        assert not standard_route.called
        assert json.loads(fast_route.calls.last.request.content) == {
            "video_url": "https://media.example.com/source.mp4",
            "resolution": "720p",
            "bitrate_level": "medium",
            "fps": 30,
        }

    @pytest.mark.parametrize(
        "overrides",
        [
            {"tool_version": "fast", "resolution": "8k"},
            {"tool_version": "fast", "resolution_limit": 4000},
            {"tool_version": "fast", "enhance_style": "hd"},
            {"resolution": "720p", "resolution_limit": 720},
            {"tool_version": "turbo"},
        ],
    )
    def test_request_rejects_profiles_the_endpoint_does_not_document(
        self, overrides: dict[str, object]
    ) -> None:
        with pytest.raises(ValidationError):
            VodMediaKitEnhancementRequest(
                video_url="https://media.example.com/source.mp4",
                **overrides,  # type: ignore[arg-type]
            )

    def test_request_rejects_unknown_fields_and_non_https(self) -> None:
        with pytest.raises(ValidationError):
            VodMediaKitEnhancementRequest.model_validate(
                {"video_url": "https://media.example.com/in.mp4", "unknown": True}
            )
        with pytest.raises(ValidationError):
            VodMediaKitEnhancementRequest(video_url="http://media.example.com/in.mp4")


class TestVodMediaKitSuccessContract:
    """Verify conservative success-envelope parsing and normalization."""

    @respx.mock
    async def test_maps_data_envelope_and_preserves_metadata(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                headers={"x-tt-logid": "log-header"},
                json={
                    "success": True,
                    "request_id": "body-request",
                    "data": {
                        "output_url": "https://output.example.com/enhanced.mp4",
                        "request_id": "nested-request",
                        "task_id": "task-1",
                        "status": "completed",
                        "mime_type": "video/mp4",
                        "expires_at": "2026-08-13T00:00:00Z",
                        "output_size_bytes": 12345,
                        "error": {"code": "WARN", "message": "provider warning"},
                    },
                },
            )
        )

        result = await service.enhance(request())

        assert result.status == "succeeded"
        assert result.request_id == "body-request"
        assert result.provider_log_id == "log-header"
        assert result.task_id == "task-1"
        assert str(result.output_url) == "https://output.example.com/enhanced.mp4"
        assert result.provider_status == "completed"
        assert result.mime_type == "video/mp4"
        assert result.expires_at == "2026-08-13T00:00:00Z"
        assert result.output_size_bytes == 12345
        assert result.failure_code == "WARN"
        assert result.failure_message == "provider warning"

    @respx.mock
    async def test_maps_observed_top_level_async_acceptance(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                headers={"x-tt-logid": "log-accepted"},
                json={
                    "success": True,
                    "task_id": "amk-tool-enhance-video-sanitized",
                    "request_id": "request-accepted",
                },
            )
        )

        result = await service.enhance(request())

        assert result.status == "accepted"
        assert result.task_id == "amk-tool-enhance-video-sanitized"
        assert result.request_id == "request-accepted"
        assert result.provider_log_id == "log-accepted"
        assert result.output_url is None

    @pytest.mark.parametrize("container", ["data", "result"])
    @pytest.mark.parametrize("url_field", ["output_url", "video_url", "url"])
    @respx.mock
    async def test_accepts_only_explicit_container_and_url_aliases(
        self,
        service: VodMediaKitEnhancementService,
        container: str,
        url_field: str,
    ) -> None:
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    container: {
                        url_field: "https://output.example.com/enhanced.mp4",
                        "id": "task-alias",
                        "content_type": "video/mp4",
                        "expiration": "2026-08-13T00:00:00Z",
                        "size": 99,
                        "future_field": "ignored",
                    },
                    "future_root_field": "ignored",
                },
            )
        )

        result = await service.enhance(request())

        assert result.task_id == "task-alias"
        assert result.mime_type == "video/mp4"
        assert result.output_size_bytes == 99

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"success": False, "error": {"message": "failed"}},
            {"success": True, "data": {}},
            {"success": True, "data": {"output_url": "http://output.example.com/out.mp4"}},
            {
                "success": True,
                "data": {
                    "output_url": "https://output.example.com/out.mp4",
                    "expires_at": "not-a-timestamp",
                },
            },
            {
                "success": True,
                "data": {"output_url": "https://output.example.com/a.mp4"},
                "result": {"output_url": "https://output.example.com/b.mp4"},
            },
        ],
    )
    @respx.mock
    async def test_rejects_unknown_or_malformed_success_bodies(
        self,
        service: VodMediaKitEnhancementService,
        body: dict[str, object],
    ) -> None:
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(200, json=body, headers={"x-tt-logid": "log-2"})
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.enhance(request())

        error = exc_info.value
        assert error.provider == "byteplus-vod-mediakit"
        assert error.code == "INVALID_RESPONSE"
        assert error.request_id == "log-2"
        assert error.retryable is False
        assert error.ambiguous_completion is False

    @respx.mock
    async def test_rejects_non_json_2xx(self, service: VodMediaKitEnhancementService) -> None:
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, text="not-json"))
        with pytest.raises(ProviderError) as exc_info:
            await service.enhance(request())
        assert exc_info.value.code == "INVALID_RESPONSE"


class TestVodMediaKitEnhancementTaskContract:
    """Verify the live-confirmed enhancement task response contract."""

    @respx.mock
    async def test_completed_task_maps_output_and_metadata(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        respx.get(TASK_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                headers={"x-tt-logid": "log-get"},
                json={
                    "success": True,
                    "task_id": "amk-tool-enhance-video-1",
                    "task_type": "enhance-video",
                    "status": "completed",
                    "result": {
                        "tool_version": "professional",
                        "video_url": "https://output.example.com/enhanced.mp4",
                        "duration": 30.917,
                        "fps": 24,
                        "resolution": "4k",
                    },
                    "expires_at": 1788962055,
                    "created_at": 1788874787,
                    "finished_at": 1788875656,
                    "request_id": "req-get",
                    "queue_id": "default",
                },
            )
        )

        result = await service.get("amk-tool-enhance-video-1")

        assert result.status == "succeeded"
        assert result.task_id == "amk-tool-enhance-video-1"
        assert result.provider_status == "completed"
        assert result.request_id == "req-get"
        assert str(result.output_url) == "https://output.example.com/enhanced.mp4"
        assert result.duration_seconds == 30.917
        assert result.fps == 24
        assert result.resolution == "4k"
        assert result.tool_version == "professional"
        assert result.created_at == "2026-09-08T13:39:47+00:00"
        assert result.finished_at == "2026-09-08T13:54:16+00:00"
        assert result.source_expires_at == "2026-09-09T13:54:15+00:00"

    @respx.mock
    async def test_fast_task_reports_fast_tier_and_fractional_fps(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        respx.get(f"{BASE_URL}/tasks/amk-tool-enhance-video-fast-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "task_id": "amk-tool-enhance-video-fast-1",
                    "task_type": "enhance-video-fast",
                    "status": "completed",
                    "result": {
                        "video_url": "https://output.example.com/fast.mp4",
                        "duration": 65.5,
                        "fps": 29.97,
                        "resolution": "720p",
                    },
                    "created_at": 1777291767,
                    "finished_at": 1777291851,
                },
            )
        )

        result = await service.get("amk-tool-enhance-video-fast-1")

        assert result.status == "succeeded"
        assert result.tool_version == "fast"
        assert result.fps == 29.97
        assert result.resolution == "720p"

    @respx.mock
    async def test_unknown_task_type_is_rejected(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        respx.get(TASK_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "task_id": "amk-tool-enhance-video-1",
                    "task_type": "transcode-video",
                    "status": "running",
                },
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.get("amk-tool-enhance-video-1")

        assert exc_info.value.code == "INVALID_RESPONSE"

    @respx.mock
    async def test_running_task_maps_to_processing(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        respx.get(TASK_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "task_id": "amk-tool-enhance-video-1",
                    "task_type": "enhance-video",
                    "status": "running",
                },
            )
        )

        result = await service.get("amk-tool-enhance-video-1")

        assert result.status == "processing"
        assert result.output_url is None

    @respx.mock
    async def test_failed_task_sanitizes_provider_error(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        respx.get(TASK_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "task_id": "amk-tool-enhance-video-1",
                    "task_type": "enhance-video",
                    "status": "failed",
                    "error": {
                        "code": "DownloadFailed",
                        "message": "Failed to fetch https://private.example.com/source.mp4?token=x",
                    },
                },
            )
        )

        result = await service.get("amk-tool-enhance-video-1")

        assert result.status == "failed"
        assert result.failure_code == "DownloadFailed"
        assert "private.example.com" not in (result.failure_message or "")
        assert "token=x" not in (result.failure_message or "")

    @pytest.mark.parametrize(
        ("status", "extra"),
        [
            (
                "completed",
                {"result": {"video_url": "https://output.example.com/enhanced.mp4"}},
            ),
            ("running", {}),
            ("failed", {"error": {"code": "Failed", "message": "Task failed."}}),
        ],
    )
    @respx.mock
    async def test_mismatched_task_id_fails_closed(
        self,
        service: VodMediaKitEnhancementService,
        status: str,
        extra: dict[str, object],
    ) -> None:
        respx.get(TASK_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                headers={"x-tt-logid": "safe-header-log"},
                json={
                    "success": True,
                    "task_id": "amk-tool-enhance-video-other",
                    "task_type": "enhance-video",
                    "status": status,
                    "request_id": "https://private.example.com/?token=secret",
                    **extra,
                },
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.get("amk-tool-enhance-video-1")

        assert exc_info.value.code == "INVALID_RESPONSE"
        assert exc_info.value.retryable is False
        assert exc_info.value.request_id == "safe-header-log"
        assert "amk-tool-enhance-video" not in exc_info.value.message
        assert "private.example.com" not in (exc_info.value.request_id or "")
        assert "token=secret" not in (exc_info.value.request_id or "")

    @pytest.mark.parametrize(
        "body",
        [
            {
                "success": True,
                "task_id": "amk-tool-enhance-video-1",
                "task_type": "transcode-video",
                "status": "completed",
                "result": {"video_url": "https://output.example.com/out.mp4"},
            },
            {
                "success": True,
                "task_id": "amk-tool-enhance-video-1",
                "task_type": "enhance-video",
                "status": "completed",
                "result": {},
            },
            {
                "success": True,
                "task_id": "amk-tool-enhance-video-1",
                "task_type": "enhance-video",
                "status": "expired",
            },
            {
                "success": True,
                "task_id": "amk-tool-enhance-video-1",
                "task_type": "enhance-video",
                "status": "https://output.example.com/video.mp4?token=secret",
            },
        ],
    )
    @respx.mock
    async def test_unrecognized_task_response_fails_closed(
        self,
        service: VodMediaKitEnhancementService,
        body: dict[str, object],
    ) -> None:
        respx.get(TASK_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                headers={"x-tt-logid": "safe-header-log"},
                json={
                    **body,
                    "request_id": "https://private.example.com/?token=secret",
                },
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.get("amk-tool-enhance-video-1")

        assert exc_info.value.code == "INVALID_RESPONSE"
        assert exc_info.value.request_id == "safe-header-log"
        assert "output.example.com" not in exc_info.value.message
        assert "token=secret" not in exc_info.value.message
        assert "private.example.com" not in (exc_info.value.request_id or "")
        assert "token=secret" not in (exc_info.value.request_id or "")


class TestVodMediaKitErrorContract:
    """Verify HTTP and transport failure normalization."""

    @respx.mock
    async def test_verified_unauthenticated_error(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(
                401,
                headers={"x-tt-logid": "log-401"},
                json={
                    "success": False,
                    "error": {
                        "code": "AuthenticationError",
                        "type": "Unauthorized",
                        "message": "The API key is missing or invalid.",
                    },
                },
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.enhance(request())

        error = exc_info.value
        assert error.provider == "byteplus-vod-mediakit"
        assert error.http_status == 401
        assert error.code == "AuthenticationError"
        assert error.request_id == "log-401"
        assert error.retryable is False
        assert error.ambiguous_completion is False

    @respx.mock
    async def test_rate_limit_preserves_retry_hint(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(
                429,
                headers={"Retry-After": "12.5"},
                json={"success": False, "error": {"message": "too many requests"}},
            )
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.enhance(request())
        assert exc_info.value.retryable is True
        assert exc_info.value.normalized.retry_after_seconds == 12.5
        assert exc_info.value.ambiguous_completion is False

    @respx.mock
    async def test_server_error_is_ambiguous_and_not_retryable(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(
                503,
                json={"success": False, "error": {"code": "BUSY", "message": "busy"}},
            )
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.enhance(request())
        assert exc_info.value.code == "BUSY"
        assert exc_info.value.retryable is False
        assert exc_info.value.ambiguous_completion is True

    @respx.mock
    async def test_error_message_redacts_urls(self, service: VodMediaKitEnhancementService) -> None:
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(
                400,
                json={
                    "success": False,
                    "error": {
                        "message": "cannot fetch https://private.example.com/video.mp4?token=secret"
                    },
                },
            )
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.enhance(request())
        assert "private.example.com" not in exc_info.value.message
        assert "secret" not in exc_info.value.message

    @pytest.mark.parametrize(
        ("exception", "code"),
        [
            (httpx.TimeoutException("timed out"), "TIMEOUT"),
            (httpx.ReadError("connection lost"), "TRANSPORT_ERROR"),
        ],
    )
    @respx.mock
    async def test_transport_failures_are_ambiguous_and_not_retryable(
        self,
        service: VodMediaKitEnhancementService,
        exception: httpx.TransportError,
        code: str,
    ) -> None:
        respx.post(ENDPOINT).mock(side_effect=exception)
        with pytest.raises(ProviderError) as exc_info:
            await service.enhance(request())
        assert exc_info.value.code == code
        assert exc_info.value.retryable is False
        assert exc_info.value.ambiguous_completion is True

    @respx.mock
    async def test_malformed_error_falls_back_without_body_leak(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        respx.post(ENDPOINT).mock(return_value=httpx.Response(400, text="sensitive body"))
        with pytest.raises(ProviderError) as exc_info:
            await service.enhance(request())
        assert exc_info.value.code == "HTTP_400"
        assert "sensitive body" not in exc_info.value.message

    @respx.mock
    async def test_redirect_is_not_accepted_as_success(
        self, service: VodMediaKitEnhancementService
    ) -> None:
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(307, headers={"Location": "https://other.example.com"})
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.enhance(request())
        assert exc_info.value.code == "HTTP_307"
        assert exc_info.value.retryable is False
