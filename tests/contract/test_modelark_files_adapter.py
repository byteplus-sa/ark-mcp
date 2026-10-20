"""Contract tests for the ModelArk Files API adapter.

Covers the multipart upload-by-URL request, polling until ``active``,
failed/timeout handling, best-effort delete, and the ``file_id`` content parts
the understanding request builder emits for uploaded files.
"""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.providers.modelark.files import (
    MAX_RETENTION_SECONDS,
    MIN_RETENTION_SECONDS,
    ModelArkFile,
    ModelArkFilesService,
)
from ark_mcp.providers.modelark.understanding import SeedUnderstandingService

MODELARK_BASE = "https://ark.ap-southeast.bytepluses.com/api/v3"
SOURCE_URL = "https://cdn.example.com/episode.mp4"


@pytest.fixture
def service() -> ModelArkFilesService:
    gateway = ModelArkGateway(
        api_key="sk-test-key",  # pragma: allowlist secret
        base_url=MODELARK_BASE,
        timeout=10.0,
        connect_timeout=5.0,
    )
    return ModelArkFilesService(gateway=gateway)


def _file(status: str, **extra: object) -> dict[str, object]:
    return {
        "object": "file",
        "id": "file-1",
        "purpose": "user_data",
        "filename": "episode.mp4",
        "mime_type": "video/mp4",
        "status": status,
        **extra,
    }


def _multipart_fields(request: httpx.Request) -> dict[str, str]:
    """Parse text fields from a multipart/form-data body."""
    content_type = request.headers["Content-Type"]
    boundary = content_type.split("boundary=", 1)[1]
    fields: dict[str, str] = {}
    for chunk in request.content.split(f"--{boundary}".encode()):
        head, sep, body = chunk.partition(b"\r\n\r\n")
        if not sep or b'name="' not in head:
            continue
        name = head.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
        fields[name] = body.rstrip(b"\r\n").decode()
    return fields


class TestUploadUrl:
    @pytest.mark.parametrize("status", [500, 502, 503])
    @respx.mock
    async def test_server_error_may_have_created_file(
        self, service: ModelArkFilesService, status: int
    ) -> None:
        respx.post(f"{MODELARK_BASE}/files").mock(
            return_value=httpx.Response(status, json={"error": {"message": "unavailable"}})
        )
        with pytest.raises(ProviderError) as info:
            await service.upload_url(SOURCE_URL)
        assert info.value.error.ambiguous_completion is True

    @respx.mock
    async def test_lost_upload_response_is_ambiguous(self, service: ModelArkFilesService) -> None:
        respx.post(f"{MODELARK_BASE}/files").mock(side_effect=httpx.ReadError("response lost"))
        with pytest.raises(ProviderError) as info:
            await service.upload_url(SOURCE_URL)
        assert info.value.error.ambiguous_completion is True
        assert info.value.error.retryable is False

    @respx.mock
    async def test_upload_timeout_is_ambiguous_not_safe_to_retry(
        self, service: ModelArkFilesService
    ) -> None:
        respx.post(f"{MODELARK_BASE}/files").mock(side_effect=httpx.ReadTimeout("timeout"))
        with pytest.raises(ProviderError) as info:
            await service.upload_url(SOURCE_URL)
        assert info.value.error.ambiguous_completion is True
        assert info.value.error.retryable is False

    @respx.mock
    async def test_sends_multipart_url_upload(self, service: ModelArkFilesService) -> None:
        route = respx.post(f"{MODELARK_BASE}/files").mock(
            return_value=httpx.Response(200, json=_file("processing"))
        )

        before = int(time.time())
        uploaded = await service.upload_url(
            SOURCE_URL, video_model="seed-2-0-lite-260428", video_fps=0.2
        )

        assert uploaded.id == "file-1"
        assert uploaded.status == "processing"
        request = route.calls.last.request
        assert request.headers["Content-Type"].startswith("multipart/form-data; boundary=")
        assert request.headers["Authorization"] == "Bearer sk-test-key"
        fields = _multipart_fields(request)
        assert fields["purpose"] == "user_data"
        assert fields["url"] == SOURCE_URL
        assert fields["preprocess_configs[video][model]"] == "seed-2-0-lite-260428"
        assert fields["preprocess_configs[video][fps]"] == "0.2"
        retention = int(fields["expire_at"]) - before
        assert MIN_RETENTION_SECONDS < retention < MIN_RETENTION_SECONDS + 3600

    @respx.mock
    async def test_retention_is_clamped_to_provider_maximum(
        self, service: ModelArkFilesService
    ) -> None:
        route = respx.post(f"{MODELARK_BASE}/files").mock(
            return_value=httpx.Response(200, json=_file("processing"))
        )

        await service.upload_url(SOURCE_URL, retention_seconds=MAX_RETENTION_SECONDS)

        fields = _multipart_fields(route.calls.last.request)
        assert int(fields["expire_at"]) - int(time.time()) < MAX_RETENTION_SECONDS
        assert "preprocess_configs[video][model]" not in fields

    @respx.mock
    async def test_error_is_normalized(self, service: ModelArkFilesService) -> None:
        respx.post(f"{MODELARK_BASE}/files").mock(
            return_value=httpx.Response(
                400, json={"error": {"code": "InvalidParameter", "message": "bad url"}}
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.upload_url(SOURCE_URL)

        assert exc_info.value.error.operation == "upload_file"
        assert exc_info.value.code == "InvalidParameter"
        assert exc_info.value.error.http_status == 400


class TestWaitUntilActive:
    @respx.mock
    async def test_polls_until_active(self, service: ModelArkFilesService) -> None:
        route = respx.get(f"{MODELARK_BASE}/files/file-1").mock(
            side_effect=[
                httpx.Response(200, json=_file("processing")),
                httpx.Response(200, json=_file("active", bytes=27131163)),
            ]
        )

        active = await service.wait_until_active(
            ModelArkFile.model_validate(_file("processing")), poll_seconds=0
        )

        assert active.status == "active"
        assert active.bytes == 27131163
        assert active.is_video
        assert route.call_count == 2

    async def test_already_active_needs_no_poll(self, service: ModelArkFilesService) -> None:
        active = await service.wait_until_active(ModelArkFile.model_validate(_file("active")))
        assert active.status == "active"

    @respx.mock
    async def test_failed_file_raises(self, service: ModelArkFilesService) -> None:
        respx.get(f"{MODELARK_BASE}/files/file-1").mock(
            return_value=httpx.Response(
                200,
                json=_file("failed", error={"code": "DownloadFailed", "message": "404 on url"}),
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.wait_until_active(
                ModelArkFile.model_validate(_file("processing")), poll_seconds=0
            )

        assert exc_info.value.code == "DownloadFailed"
        assert "404 on url" in exc_info.value.message
        assert exc_info.value.error.retryable is False

    @respx.mock
    async def test_timeout_raises_retryable(self, service: ModelArkFilesService) -> None:
        respx.get(f"{MODELARK_BASE}/files/file-1").mock(
            return_value=httpx.Response(200, json=_file("processing"))
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.wait_until_active(
                ModelArkFile.model_validate(_file("processing")),
                timeout_seconds=0,
                poll_seconds=0,
            )

        assert exc_info.value.code == "FILE_PROCESSING_TIMEOUT"
        assert exc_info.value.error.retryable is True


class TestDelete:
    @respx.mock
    async def test_delete_success(self, service: ModelArkFilesService) -> None:
        respx.delete(f"{MODELARK_BASE}/files/file-1").mock(
            return_value=httpx.Response(200, json={"id": "file-1", "deleted": True})
        )
        assert await service.delete("file-1") is True

    @respx.mock
    async def test_delete_failure_never_raises(self, service: ModelArkFilesService) -> None:
        respx.delete(f"{MODELARK_BASE}/files/file-1").mock(
            return_value=httpx.Response(404, json={"error": {"message": "not found"}})
        )
        assert await service.delete("file-1") is False

    @respx.mock
    async def test_delete_transport_error_never_raises(self, service: ModelArkFilesService) -> None:
        respx.delete(f"{MODELARK_BASE}/files/file-1").mock(side_effect=httpx.ConnectError("down"))
        assert await service.delete("file-1") is False


class TestFileIdContentParts:
    def test_audio_and_video_file_ids(self) -> None:
        request = SeedUnderstandingService.build_request(
            model="seed-2-0-lite-260428",
            prompt="Describe the audio.",
            audio_parts=[
                {"kind": "file_id", "file_id": "file-audio"},
                {"kind": "video_file_id", "file_id": "file-video"},
            ],
        )
        content = request.model_dump(exclude_none=True)["messages"][-1]["content"]
        assert content[0] == {"type": "input_audio", "input_audio": {"file_id": "file-audio"}}
        assert content[1] == {"type": "video_url", "video_url": {"file_id": "file-video"}}
        assert content[-1] == {"type": "text", "text": "Describe the audio."}
