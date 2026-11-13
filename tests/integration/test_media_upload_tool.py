"""Integration tests for the ``media_upload`` tool.

Uses the shared ``test_env`` / ``fake_ctx`` fixtures so the runtime
(budget ledger, provider limiters) is real.  The ``TosGateway`` is mocked
via ``patch`` so no SDK client or network is involved.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.tools.media_upload import (
    MediaUploadInput,
    MediaUploadItem,
    MediaUploadOutput,
    media_upload,
)
from tests.fixtures.fake_context import FakeContext

_GATEWAY = "ark_mcp.tools.media_upload.make_object_storage_gateway"


def _mock_gateway() -> AsyncMock:
    gw = AsyncMock()
    gw.upload_bytes = AsyncMock(return_value=None)
    gw.upload_file = AsyncMock(return_value=None)
    gw.presign_get = AsyncMock(return_value="https://tos.example.com/presigned-url")
    gw.close = AsyncMock()
    return gw


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


async def _upload(
    ctx: FakeContext,
    gateway: AsyncMock,
    *items: MediaUploadItem,
    expires_in_seconds: int | None = None,
) -> MediaUploadOutput:
    with patch(_GATEWAY, return_value=gateway):
        return await media_upload(
            MediaUploadInput(items=list(items), expires_in_seconds=expires_in_seconds), ctx
        )


class TestMediaUploadBase64:
    async def test_base64_upload_success(self, test_env: None, fake_ctx: FakeContext) -> None:
        mock_gw = _mock_gateway()

        result = await _upload(
            fake_ctx,
            mock_gw,
            MediaUploadItem(
                media_type="video", mime_type="video/mp4", data=_b64(b"fake-video-bytes")
            ),
        )

        assert isinstance(result, MediaUploadOutput)
        assert result.succeeded == 1
        assert result.failed == 0
        assert result.total_bytes == len(b"fake-video-bytes")
        item = result.items[0]
        assert item.index == 0
        assert item.error is None
        assert item.retryable is None
        assert item.url == "https://tos.example.com/presigned-url"
        assert item.bytes == len(b"fake-video-bytes")
        assert item.mime_type == "video/mp4"
        assert item.expires_at
        assert item.object_key is not None
        assert "references/video/" in item.object_key
        mock_gw.upload_bytes.assert_called_once()
        mock_gw.presign_get.assert_called_once()
        mock_gw.close.assert_called_once()

    async def test_base64_upload_with_custom_prefix(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        result = await _upload(
            fake_ctx,
            _mock_gateway(),
            MediaUploadItem(
                media_type="image",
                mime_type="image/png",
                data=_b64(b"img"),
                key_prefix="uploads/2026",
            ),
        )

        assert result.items[0].object_key is not None
        assert "uploads/2026/image/" in result.items[0].object_key

    async def test_base64_upload_with_custom_expiry(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()

        result = await _upload(
            fake_ctx,
            mock_gw,
            MediaUploadItem(media_type="image", mime_type="image/png", data=_b64(b"img")),
            expires_in_seconds=3600,
        )

        assert result.succeeded == 1
        mock_gw.presign_get.assert_called_once_with(key=result.items[0].object_key, expires=3600)

    async def test_data_item_without_mime_type_rejected(self, test_env: None) -> None:
        with pytest.raises(ValidationError, match="mime_type is required"):
            MediaUploadItem(media_type="image", data=_b64(b"img"))


class TestMediaUploadFilePath:
    async def test_file_path_upload_success(
        self, test_env: None, fake_ctx: FakeContext, tmp_path: Path
    ) -> None:
        video_file = tmp_path / "clip.mp4"
        video_file.write_bytes(b"fake-video-content")
        mock_gw = _mock_gateway()

        result = await _upload(
            fake_ctx,
            mock_gw,
            MediaUploadItem(media_type="video", mime_type="video/mp4", file_path=str(video_file)),
        )

        assert isinstance(result, MediaUploadOutput)
        assert result.succeeded == 1
        assert result.items[0].bytes == len(b"fake-video-content")
        assert result.items[0].file_path == str(video_file)
        mock_gw.upload_file.assert_called_once()
        mock_gw.upload_bytes.assert_not_called()

    async def test_file_path_mime_type_inferred_from_extension(
        self, test_env: None, fake_ctx: FakeContext, tmp_path: Path
    ) -> None:
        video_file = tmp_path / "clip.mp4"
        video_file.write_bytes(b"fake-video-content")
        mock_gw = _mock_gateway()

        result = await _upload(
            fake_ctx, mock_gw, MediaUploadItem(media_type="video", file_path=str(video_file))
        )

        assert result.succeeded == 1
        assert result.items[0].mime_type == "video/mp4"
        assert mock_gw.upload_file.call_args.kwargs["mime_type"] == "video/mp4"

    async def test_file_path_uninferable_mime_type_reported_per_item(
        self, test_env: None, fake_ctx: FakeContext, tmp_path: Path
    ) -> None:
        unknown_file = tmp_path / "clip.unknownext"
        unknown_file.write_bytes(b"data")
        mock_gw = _mock_gateway()

        result = await _upload(
            fake_ctx, mock_gw, MediaUploadItem(media_type="video", file_path=str(unknown_file))
        )

        assert result.failed == 1
        assert result.items[0].error is not None
        assert "Could not infer mime_type" in result.items[0].error
        assert result.items[0].retryable is False
        mock_gw.upload_file.assert_not_called()

    async def test_file_path_nonexistent_reported_per_item(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()

        result = await _upload(
            fake_ctx,
            mock_gw,
            MediaUploadItem(
                media_type="video", mime_type="video/mp4", file_path="/nonexistent/path/video.mp4"
            ),
        )

        assert result.succeeded == 0
        assert result.failed == 1
        assert result.items[0].error is not None
        assert "File not found" in result.items[0].error
        assert result.items[0].retryable is False
        assert result.items[0].url is None
        mock_gw.upload_file.assert_not_called()

    async def test_file_path_rejected_in_http_mode(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ark_mcp.config.env import get_settings

        http_settings = get_settings().model_copy(update={"mcp_transport": "http"})
        monkeypatch.setattr("ark_mcp.tools.media_upload.get_settings", lambda: http_settings)

        video_file = tmp_path / "clip.mp4"
        video_file.write_bytes(b"data")
        mock_gw = _mock_gateway()

        result = await _upload(
            fake_ctx,
            mock_gw,
            MediaUploadItem(media_type="video", mime_type="video/mp4", file_path=str(video_file)),
        )

        assert result.failed == 1
        assert result.items[0].error is not None
        assert "stdio transport" in result.items[0].error
        mock_gw.upload_file.assert_not_called()

    async def test_file_path_over_media_limit_reported_per_item(
        self, test_env: None, fake_ctx: FakeContext, tmp_path: Path
    ) -> None:
        image_file = tmp_path / "big.png"
        image_file.write_bytes(b"x" * (11 * 1024 * 1024))
        mock_gw = _mock_gateway()

        result = await _upload(
            fake_ctx,
            mock_gw,
            MediaUploadItem(media_type="image", mime_type="image/png", file_path=str(image_file)),
        )

        assert result.failed == 1
        assert result.items[0].error is not None
        assert "exceeds" in result.items[0].error
        mock_gw.upload_file.assert_not_called()


class TestMediaUploadValidation:
    async def test_oversized_base64_reported_per_item(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()

        result = await _upload(
            fake_ctx,
            mock_gw,
            MediaUploadItem(
                media_type="image", mime_type="image/png", data=_b64(b"x" * (11 * 1024 * 1024))
            ),
        )

        assert result.failed == 1
        assert result.items[0].error is not None
        assert "exceeds limit" in result.items[0].error
        mock_gw.upload_bytes.assert_not_called()

    async def test_invalid_mime_reported_per_item(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()

        result = await _upload(
            fake_ctx,
            mock_gw,
            MediaUploadItem(media_type="video", mime_type="text/plain", data=_b64(b"data")),
        )

        assert result.failed == 1
        assert result.items[0].error is not None
        assert "not allowed" in result.items[0].error
        mock_gw.upload_bytes.assert_not_called()

    async def test_both_data_and_file_path_reported_per_item(
        self, test_env: None, fake_ctx: FakeContext, tmp_path: Path
    ) -> None:
        video_file = tmp_path / "f.mp4"
        video_file.write_bytes(b"data")

        result = await _upload(
            fake_ctx,
            _mock_gateway(),
            MediaUploadItem(
                media_type="video",
                mime_type="video/mp4",
                data=_b64(b"data"),
                file_path=str(video_file),
            ),
        )

        assert result.failed == 1
        assert result.items[0].error is not None
        assert "exactly one" in result.items[0].error

    async def test_neither_data_nor_file_path_reported_per_item(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        result = await _upload(
            fake_ctx, _mock_gateway(), MediaUploadItem(media_type="video", mime_type="video/mp4")
        )

        assert result.failed == 1
        assert result.items[0].error is not None
        assert "exactly one" in result.items[0].error

    async def test_key_prefix_injection_reported_per_item(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()

        result = await _upload(
            fake_ctx,
            mock_gw,
            MediaUploadItem(
                media_type="video",
                mime_type="video/mp4",
                data=_b64(b"data"),
                key_prefix="../../../etc/passwd",
            ),
        )

        assert result.failed == 1
        assert result.items[0].error is not None
        assert "key_prefix" in result.items[0].error
        mock_gw.upload_bytes.assert_not_called()

    async def test_empty_items_rejected(self, test_env: None) -> None:
        with pytest.raises(ValidationError):
            MediaUploadInput(items=[])

    async def test_expiry_out_of_range_rejected(self, test_env: None) -> None:
        item = MediaUploadItem(media_type="image", mime_type="image/png", data=_b64(b"img"))
        with pytest.raises(ValidationError):
            MediaUploadInput(items=[item], expires_in_seconds=59)
        with pytest.raises(ValidationError):
            MediaUploadInput(items=[item], expires_in_seconds=604801)


class TestMediaUploadBatch:
    async def test_failing_item_does_not_block_others(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()

        result = await _upload(
            fake_ctx,
            mock_gw,
            MediaUploadItem(media_type="image", mime_type="image/png", data=_b64(b"one")),
            MediaUploadItem(media_type="video", mime_type="text/plain", data=_b64(b"bad")),
            MediaUploadItem(media_type="audio", mime_type="audio/wav", data=_b64(b"three")),
        )

        assert result.succeeded == 2
        assert result.failed == 1
        assert result.total_bytes == len(b"one") + len(b"three")
        assert [item.index for item in result.items] == [0, 1, 2]
        assert result.items[0].error is None
        assert result.items[1].error is not None
        assert result.items[1].url is None
        assert result.items[2].error is None
        assert result.items[2].object_key is not None
        assert "references/audio/" in result.items[2].object_key
        assert mock_gw.upload_bytes.call_count == 2
        mock_gw.close.assert_called_once()

    async def test_aggregate_size_cap_raises_before_upload(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ark_mcp.config.env import get_settings

        capped = get_settings().model_copy(update={"media_upload_batch_max_bytes": 5})
        monkeypatch.setattr("ark_mcp.tools.media_upload.get_settings", lambda: capped)
        mock_gw = _mock_gateway()

        with pytest.raises(ValueError, match="MEDIA_UPLOAD_BATCH_MAX_BYTES"):
            await _upload(
                fake_ctx,
                mock_gw,
                MediaUploadItem(media_type="image", mime_type="image/png", data=_b64(b"abc")),
                MediaUploadItem(media_type="image", mime_type="image/png", data=_b64(b"def")),
            )

        mock_gw.upload_bytes.assert_not_called()


class TestMediaUploadErrors:
    async def test_no_object_storage_credentials_raises(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ark_mcp.config.env import get_settings

        no_storage = get_settings().model_copy(
            update={
                "tos_access_key": "",
                "tos_secret_key": "",
                "tos_bucket": "",
                "s3_access_key": "",
                "s3_secret_key": "",
                "s3_bucket": "",
            }
        )
        monkeypatch.setattr("ark_mcp.tools.media_upload.get_settings", lambda: no_storage)

        with pytest.raises(ValueError, match="Object storage is not configured"):
            await _upload(
                fake_ctx,
                _mock_gateway(),
                MediaUploadItem(media_type="video", mime_type="video/mp4", data=_b64(b"data")),
            )

    async def test_provider_error_reported_per_item(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()
        mock_gw.upload_bytes = AsyncMock(
            side_effect=ProviderError(
                NormalizedProviderError(
                    provider="tos",
                    operation="upload",
                    http_status=500,
                    code="INTERNAL",
                    message="TOS internal error",
                    retryable=False,
                )
            )
        )

        result = await _upload(
            fake_ctx,
            mock_gw,
            MediaUploadItem(media_type="video", mime_type="video/mp4", data=_b64(b"data")),
        )

        assert result.succeeded == 0
        assert result.failed == 1
        assert result.total_bytes == 0
        assert result.items[0].error == "TOS internal error"
        assert result.items[0].retryable is False
        assert result.items[0].url is None
        mock_gw.close.assert_called_once()


class TestMediaUploadS3Backend:
    async def test_s3_backend_uses_s3_provider_slot(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ark_mcp.config.env import get_settings

        s3_settings = get_settings().model_copy(
            update={
                "tos_access_key": "",
                "tos_secret_key": "",
                "tos_bucket": "",
                "s3_access_key": "ak-s3",
                "s3_secret_key": "sk-s3",  # pragma: allowlist secret
                "s3_bucket": "s3-bucket",
                "object_storage_backend": "s3",
            }
        )
        monkeypatch.setattr("ark_mcp.tools.media_upload.get_settings", lambda: s3_settings)

        result = await _upload(
            fake_ctx,
            _mock_gateway(),
            MediaUploadItem(media_type="image", mime_type="image/png", data=_b64(b"fake")),
        )

        assert isinstance(result, MediaUploadOutput)
        assert result.items[0].url == "https://tos.example.com/presigned-url"
