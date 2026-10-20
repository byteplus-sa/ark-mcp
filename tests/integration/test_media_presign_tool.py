"""Integration tests for the ``media_presign`` tool.

Uses the shared ``test_env`` / ``fake_ctx`` fixtures so the runtime
(budget ledger, provider limiters) is real.  The object-storage gateway is
mocked via ``patch`` so no SDK client or network is involved.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.tools.media_presign import (
    MediaPresignInput,
    MediaPresignOutput,
    media_presign,
)
from ark_mcp.tools.media_upload import MediaUploadInput, MediaUploadItem, media_upload
from tests.fixtures.fake_context import FakeContext

_GATEWAY = "ark_mcp.tools.media_presign.make_object_storage_gateway"
_UPLOAD_GATEWAY = "ark_mcp.tools.media_upload.make_object_storage_gateway"
_VALID_KEY = "references/video/abc-123-def"


def _mock_gateway() -> AsyncMock:
    gw = AsyncMock()
    gw.presign_get = AsyncMock(return_value="https://s3.example.com/fresh-presigned-url")
    gw.close = AsyncMock()
    return gw


def _upload_gateway() -> AsyncMock:
    gw = AsyncMock()
    gw.upload_bytes = AsyncMock(return_value=None)
    gw.presign_get = AsyncMock(return_value="https://tos.example.com/original-url")
    gw.close = AsyncMock()
    return gw


def _video_upload_input() -> MediaUploadInput:
    return MediaUploadInput(
        items=[
            MediaUploadItem(
                media_type="video",
                mime_type="video/mp4",
                data=base64.b64encode(b"fake-video-bytes").decode(),
            )
        ]
    )


def _principal(principal_id: str) -> AuthContext:
    return AuthContext(principal_id=principal_id, tenant_id="tenant-a", transport="http")


async def _presign(
    ctx: FakeContext,
    gateway: AsyncMock,
    *object_keys: str,
    expires_in_seconds: int | None = None,
) -> MediaPresignOutput:
    with patch(_GATEWAY, return_value=gateway):
        return await media_presign(
            MediaPresignInput(object_keys=list(object_keys), expires_in_seconds=expires_in_seconds),
            ctx,
        )


class TestMediaPresignSuccess:
    async def test_presign_returns_fresh_url(self, test_env: None, fake_ctx: FakeContext) -> None:
        mock_gw = _mock_gateway()

        result = await _presign(fake_ctx, mock_gw, _VALID_KEY)

        assert isinstance(result, MediaPresignOutput)
        assert result.succeeded == 1
        assert result.failed == 0
        item = result.items[0]
        assert item.url == "https://s3.example.com/fresh-presigned-url"
        assert item.object_key == _VALID_KEY
        assert item.expires_at
        assert item.code is None
        assert item.error is None
        mock_gw.presign_get.assert_called_once_with(key=_VALID_KEY)
        mock_gw.close.assert_called_once()

    async def test_presign_with_custom_expiry(self, test_env: None, fake_ctx: FakeContext) -> None:
        mock_gw = _mock_gateway()

        result = await _presign(fake_ctx, mock_gw, _VALID_KEY, expires_in_seconds=3600)

        assert result.succeeded == 1
        mock_gw.presign_get.assert_called_once_with(key=_VALID_KEY, expires=3600)

    async def test_presign_with_custom_prefix_key(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        key = "uploads/2026/audio/my-file-uuid"

        result = await _presign(fake_ctx, _mock_gateway(), key)

        assert result.succeeded == 1
        assert result.items[0].object_key == key

    async def test_presign_multiple_keys_preserves_order(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        keys = ["references/video/a", "references/image/b", "references/audio/c"]
        mock_gw = _mock_gateway()

        result = await _presign(fake_ctx, mock_gw, *keys)

        assert result.succeeded == 3
        assert [item.object_key for item in result.items] == keys
        assert mock_gw.presign_get.call_count == 3
        mock_gw.close.assert_called_once()

    async def test_presign_progress_reported(self, test_env: None, fake_ctx: FakeContext) -> None:
        await _presign(fake_ctx, _mock_gateway(), _VALID_KEY)

        assert (10, 100) in fake_ctx.progress_reports
        assert (30, 100) in fake_ctx.progress_reports
        assert (100, 100) in fake_ctx.progress_reports


class TestMediaPresignTosBackend:
    async def test_tos_backend_uses_tos_provider_slot(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ark_mcp.config.env import get_settings

        tos_settings = get_settings().model_copy(
            update={
                "tos_access_key": "ak-tos",
                "tos_secret_key": "sk-tos",  # pragma: allowlist secret
                "tos_bucket": "tos-bucket",
                "object_storage_backend": "tos",
            }
        )
        monkeypatch.setattr("ark_mcp.tools.media_presign.get_settings", lambda: tos_settings)

        result = await _presign(fake_ctx, _mock_gateway(), _VALID_KEY)

        assert result.succeeded == 1
        assert result.items[0].url == "https://s3.example.com/fresh-presigned-url"
        assert result.items[0].object_key == _VALID_KEY

    async def test_tos_backend_presign_after_upload_workflow(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        upload_gw = _upload_gateway()
        presign_gw = _mock_gateway()
        presign_gw.presign_get = AsyncMock(return_value="https://tos.example.com/renewed-url")

        with patch(_UPLOAD_GATEWAY, return_value=upload_gw):
            upload_result = await media_upload(_video_upload_input(), fake_ctx)
        uploaded = upload_result.items[0]
        assert uploaded.object_key is not None

        presign_result = await _presign(fake_ctx, presign_gw, uploaded.object_key)

        assert uploaded.url == "https://tos.example.com/original-url"
        assert presign_result.items[0].url == "https://tos.example.com/renewed-url"
        assert presign_result.items[0].object_key == uploaded.object_key
        upload_gw.upload_bytes.assert_called_once()
        presign_gw.presign_get.assert_called_once_with(key=uploaded.object_key)


class TestMediaPresignS3Backend:
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
        monkeypatch.setattr("ark_mcp.tools.media_presign.get_settings", lambda: s3_settings)

        result = await _presign(fake_ctx, _mock_gateway(), _VALID_KEY)

        assert result.succeeded == 1
        assert result.items[0].url == "https://s3.example.com/fresh-presigned-url"


class TestMediaPresignValidation:
    @pytest.mark.parametrize(
        ("object_key", "message"),
        [
            ("../../../etc/passwd", "object_key must contain only"),
            ("/references/video/abc", "object_key must contain only"),
            ("-references/video/abc", "object_key must contain only"),
            ("references//video/abc", "empty path segments"),
            ("references/video/abc/", "empty path segments"),
            ("", "object_key must contain only"),
            ("references/video/file.txt", "object_key must contain only"),
            ("references/video/my file", "object_key must contain only"),
        ],
        ids=[
            "path_traversal",
            "leading_slash",
            "leading_dash",
            "empty_segments",
            "trailing_slash",
            "empty_key",
            "dot_in_key",
            "space_in_key",
        ],
    )
    async def test_invalid_key_reported_per_item(
        self, test_env: None, fake_ctx: FakeContext, object_key: str, message: str
    ) -> None:
        mock_gw = _mock_gateway()

        result = await _presign(fake_ctx, mock_gw, object_key)

        assert result.succeeded == 0
        assert result.failed == 1
        item = result.items[0]
        assert item.object_key == object_key
        assert item.code == "INVALID_KEY"
        assert item.error is not None
        assert message in item.error
        assert item.url is None
        mock_gw.presign_get.assert_not_called()

    async def test_invalid_key_does_not_block_valid_keys(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()

        result = await _presign(fake_ctx, mock_gw, _VALID_KEY, "../../../etc/passwd")

        assert result.succeeded == 1
        assert result.failed == 1
        assert result.items[0].url == "https://s3.example.com/fresh-presigned-url"
        assert result.items[1].code == "INVALID_KEY"
        mock_gw.presign_get.assert_called_once_with(key=_VALID_KEY)

    async def test_empty_key_list_rejected(self, test_env: None) -> None:
        with pytest.raises(ValidationError):
            MediaPresignInput(object_keys=[])

    async def test_expiry_below_minimum_rejected(self, test_env: None) -> None:
        with pytest.raises(ValidationError):
            MediaPresignInput(object_keys=[_VALID_KEY], expires_in_seconds=59)

    async def test_expiry_above_maximum_rejected(self, test_env: None) -> None:
        with pytest.raises(ValidationError):
            MediaPresignInput(object_keys=[_VALID_KEY], expires_in_seconds=604801)


class TestMediaPresignErrors:
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
        monkeypatch.setattr("ark_mcp.tools.media_presign.get_settings", lambda: no_storage)

        with pytest.raises(ValueError, match="Object storage is not configured"):
            await _presign(fake_ctx, _mock_gateway(), _VALID_KEY)

    async def test_provider_error_reported_per_item(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()
        mock_gw.presign_get = AsyncMock(
            side_effect=ProviderError(
                NormalizedProviderError(
                    provider="s3",
                    operation="presign",
                    http_status=403,
                    code="AccessDenied",
                    message="Access denied",
                    retryable=False,
                    request_id="req-123",
                )
            )
        )

        result = await _presign(fake_ctx, mock_gw, _VALID_KEY)

        assert result.succeeded == 0
        assert result.failed == 1
        item = result.items[0]
        assert item.code == "AccessDenied"
        assert item.error == "Access denied"
        assert item.request_id == "req-123"
        assert item.url is None
        mock_gw.close.assert_called_once()

    async def test_provider_error_5xx_is_retryable(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()
        call_count = 0

        async def _fail_then_succeed(*, key: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ProviderError(
                    NormalizedProviderError(
                        provider="s3",
                        operation="presign",
                        http_status=500,
                        code="InternalError",
                        message="S3 internal error",
                        retryable=True,
                    )
                )
            return "https://s3.example.com/recovered-url"

        mock_gw.presign_get = _fail_then_succeed

        result = await _presign(fake_ctx, mock_gw, _VALID_KEY)

        assert result.succeeded == 1
        assert result.items[0].url == "https://s3.example.com/recovered-url"
        assert call_count == 2


class TestMediaPresignOwnership:
    async def test_unknown_key_rejected_for_remote_principal(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ark_mcp.tools.media_presign.get_principal", lambda _ctx: _principal("alice")
        )
        mock_gw = _mock_gateway()

        result = await _presign(fake_ctx, mock_gw, _VALID_KEY)

        assert result.failed == 1
        assert result.items[0].code == "NOT_OWNED"
        assert result.items[0].error is not None
        assert "not owned" in result.items[0].error
        assert result.items[0].url is None
        mock_gw.presign_get.assert_not_called()

    async def test_cross_principal_presign_rejected(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ark_mcp.tools.media_upload.get_principal", lambda _ctx: _principal("bob")
        )
        with patch(_UPLOAD_GATEWAY, return_value=_upload_gateway()):
            uploaded = await media_upload(_video_upload_input(), fake_ctx)
        object_key = uploaded.items[0].object_key
        assert object_key is not None

        monkeypatch.setattr(
            "ark_mcp.tools.media_presign.get_principal", lambda _ctx: _principal("alice")
        )
        presign_gw = _mock_gateway()
        denied = await _presign(fake_ctx, presign_gw, object_key)

        assert denied.failed == 1
        assert denied.items[0].code == "NOT_OWNED"
        assert denied.items[0].error is not None
        assert "not owned" in denied.items[0].error
        presign_gw.presign_get.assert_not_called()

        monkeypatch.setattr(
            "ark_mcp.tools.media_presign.get_principal", lambda _ctx: _principal("bob")
        )
        allowed = await _presign(fake_ctx, presign_gw, object_key)

        assert allowed.succeeded == 1
        assert allowed.items[0].url == "https://s3.example.com/fresh-presigned-url"
        presign_gw.presign_get.assert_called_once_with(key=object_key)
