"""Integration tests for the ``media_presign`` tool.

Uses the shared ``test_env`` / ``fake_ctx`` fixtures so the runtime is real,
with the object-storage gateway mocked via ``patch``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.tools.media_presign import (
    MediaPresignInput,
    MediaPresignOutput,
    media_presign,
)
from tests.fixtures.fake_context import FakeContext


def _mock_gateway(urls: list[str] | None = None) -> AsyncMock:
    gw = AsyncMock()
    urls = urls or ["https://s3.example.com/fresh-url"]
    gw.presign_get = AsyncMock(side_effect=urls)
    gw.close = AsyncMock()
    return gw


_VALID_KEY = "references/video/abc-123-def"
_VALID_KEY_2 = "references/image/def-456-ghi"


class TestMediaPresignBatchSuccess:
    async def test_batch_presign_returns_per_key_urls(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway(["https://s3.example.com/url-1", "https://s3.example.com/url-2"])

        with patch(
            "ark_mcp.tools.media_presign.make_object_storage_gateway",
            return_value=mock_gw,
        ):
            result = await media_presign(
                MediaPresignInput(object_keys=[_VALID_KEY, _VALID_KEY_2]),
                fake_ctx,
            )

        assert isinstance(result, MediaPresignOutput)
        assert result.succeeded == 2
        assert result.failed == 0
        assert result.items[0].object_key == _VALID_KEY
        assert result.items[0].url == "https://s3.example.com/url-1"
        assert result.items[0].expires_at
        assert result.items[1].url == "https://s3.example.com/url-2"
        assert mock_gw.presign_get.call_count == 2
        mock_gw.close.assert_called_once()

    async def test_batch_presign_with_custom_expiry(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()

        with patch(
            "ark_mcp.tools.media_presign.make_object_storage_gateway",
            return_value=mock_gw,
        ):
            result = await media_presign(
                MediaPresignInput(object_keys=[_VALID_KEY], expires_in_seconds=3600),
                fake_ctx,
            )

        assert isinstance(result, MediaPresignOutput)
        mock_gw.presign_get.assert_called_once_with(key=_VALID_KEY, expires=3600)


class TestMediaPresignBatchPartialFailure:
    async def test_malformed_key_reported_inline(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()

        with patch(
            "ark_mcp.tools.media_presign.make_object_storage_gateway",
            return_value=mock_gw,
        ):
            result = await media_presign(
                MediaPresignInput(object_keys=["../bad/key", _VALID_KEY]),
                fake_ctx,
            )

        assert isinstance(result, MediaPresignOutput)
        assert result.succeeded == 1
        assert result.failed == 1
        assert result.items[0].url is None
        assert result.items[0].code == "INVALID_KEY"
        assert result.items[1].url == "https://s3.example.com/fresh-url"
        mock_gw.presign_get.assert_called_once_with(key=_VALID_KEY)

    async def test_provider_error_reported_inline(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()

        async def _fail_then_succeed(*, key: str, **kwargs: object) -> str:
            if key == _VALID_KEY:
                raise ProviderError(
                    NormalizedProviderError(
                        provider="s3",
                        operation="presign",
                        http_status=403,
                        code="AccessDenied",
                        message="Access denied",
                        request_id="req-presign-001",
                        retryable=False,
                    )
                )
            return "https://s3.example.com/ok-url"

        mock_gw.presign_get = _fail_then_succeed

        with patch(
            "ark_mcp.tools.media_presign.make_object_storage_gateway",
            return_value=mock_gw,
        ):
            result = await media_presign(
                MediaPresignInput(object_keys=[_VALID_KEY, _VALID_KEY_2]),
                fake_ctx,
            )

        assert isinstance(result, MediaPresignOutput)
        assert result.succeeded == 1
        assert result.failed == 1
        assert result.items[0].url is None
        assert result.items[0].code == "AccessDenied"
        assert result.items[0].request_id == "req-presign-001"
        assert result.items[1].url == "https://s3.example.com/ok-url"

    async def test_unexpected_error_reported_inline_as_internal(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        mock_gw = _mock_gateway()

        async def _boom(*, key: str, **kwargs: object) -> str:
            raise AssertionError("retry loop exited unexpectedly")

        mock_gw.presign_get = _boom

        with patch(
            "ark_mcp.tools.media_presign.make_object_storage_gateway",
            return_value=mock_gw,
        ):
            result = await media_presign(
                MediaPresignInput(object_keys=[_VALID_KEY, _VALID_KEY_2]),
                fake_ctx,
            )

        assert isinstance(result, MediaPresignOutput)
        assert result.succeeded == 0
        assert result.failed == 2
        assert all(item.code == "INTERNAL" for item in result.items)


class TestMediaPresignBatchOwnership:
    async def test_unknown_key_rejected_for_remote_principal(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ark_mcp.security.auth_context import AuthContext

        monkeypatch.setattr(
            "ark_mcp.tools.media_presign.get_principal",
            lambda _ctx: AuthContext(principal_id="alice", tenant_id="tenant-a", transport="http"),
        )
        mock_gw = _mock_gateway()

        with patch(
            "ark_mcp.tools.media_presign.make_object_storage_gateway",
            return_value=mock_gw,
        ):
            result = await media_presign(
                MediaPresignInput(object_keys=[_VALID_KEY]),
                fake_ctx,
            )

        assert isinstance(result, MediaPresignOutput)
        assert result.succeeded == 0
        assert result.failed == 1
        assert result.items[0].code == "NOT_OWNED"
        assert "not owned" in result.items[0].error
        mock_gw.presign_get.assert_not_called()


class TestMediaPresignBatchErrors:
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
            await media_presign(
                MediaPresignInput(object_keys=[_VALID_KEY]),
                fake_ctx,
            )

    async def test_empty_object_keys_rejected(self, test_env: None) -> None:
        with pytest.raises(ValueError):
            MediaPresignInput(object_keys=[])

    async def test_too_many_object_keys_rejected(self, test_env: None) -> None:
        with pytest.raises(ValueError):
            MediaPresignInput(object_keys=[_VALID_KEY] * 101)
