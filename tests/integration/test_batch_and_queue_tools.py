"""media_upload_batch and derived Seedance queue info.

Batch task polling is covered in ``test_gen_task_tools.py``.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.schemas import GenerationTaskResponse
from ark_mcp.providers.modelark.seedance_queue import queue_info
from ark_mcp.tools.media_upload_batch import (
    MediaUploadBatchInput,
    MediaUploadBatchOutput,
    media_upload_batch,
)
from tests.fixtures.fake_context import FakeContext

VIDEO_URL = "https://tos-ap-southeast.bytepluses.com/video.mp4"


def _gateway(fail_keys: tuple[str, ...] = ()) -> AsyncMock:
    gw = AsyncMock()

    async def upload_bytes(*, key: str, data: bytes, mime_type: str) -> None:
        if any(marker in mime_type for marker in fail_keys):
            raise ProviderError(
                NormalizedProviderError(
                    provider="tos",
                    operation="upload",
                    code="SERVER_ERROR",
                    message="bucket unavailable",
                    retryable=False,
                )
            )

    gw.upload_bytes = AsyncMock(side_effect=upload_bytes)
    gw.upload_file = AsyncMock(return_value=None)
    gw.presign_get = AsyncMock(return_value="https://tos.example.com/presigned")
    gw.close = AsyncMock()
    return gw


# --- media_upload_batch --------------------------------------------------------


async def test_batch_uploads_items_and_reports_failures_per_item(
    test_env: None, fake_ctx: FakeContext, tmp_path: Path
) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video-bytes")
    gw = _gateway(fail_keys=("webp",))
    with patch("ark_mcp.tools.media_upload_batch.make_object_storage_gateway", return_value=gw):
        result = await media_upload_batch(
            MediaUploadBatchInput(
                items=[
                    {"media_type": "video", "file_path": str(clip)},
                    {
                        "media_type": "image",
                        "mime_type": "image/png",
                        "data": base64.b64encode(b"png").decode(),
                    },
                    {
                        "media_type": "image",
                        "mime_type": "image/webp",
                        "data": base64.b64encode(b"webp").decode(),
                    },
                    {"media_type": "image", "file_path": str(tmp_path / "missing.png")},
                ]
            ),
            fake_ctx,
        )
    assert isinstance(result, MediaUploadBatchOutput)
    assert (result.succeeded, result.failed) == (2, 2)
    first, second, third, fourth = result.items
    assert first.mime_type == "video/mp4"  # inferred from the extension
    assert first.url == "https://tos.example.com/presigned"
    assert second.object_key is not None
    assert third.error == "bucket unavailable"
    assert fourth.error == "File not found."
    gw.close.assert_called_once()


async def test_batch_rejects_total_above_cap_before_uploading(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEDIA_UPLOAD_BATCH_MAX_BYTES", "4")
    get_settings.cache_clear()
    gw = _gateway()
    with (
        patch("ark_mcp.tools.media_upload_batch.make_object_storage_gateway", return_value=gw),
        pytest.raises(ValueError, match="MEDIA_UPLOAD_BATCH_MAX_BYTES"),
    ):
        await media_upload_batch(
            MediaUploadBatchInput(
                items=[
                    {
                        "media_type": "image",
                        "mime_type": "image/png",
                        "data": base64.b64encode(b"more than four").decode(),
                    }
                ]
            ),
            fake_ctx,
        )
    gw.upload_bytes.assert_not_called()


# --- queue info helpers --------------------------------------------------------


def _task(task_id: str, status: str, **extra: Any) -> GenerationTaskResponse:
    return GenerationTaskResponse(
        id=task_id,
        model="dreamina-seedance-2-0-260128",
        status=status,
        created_at=1721400000,
        updated_at=1721400100,
        **extra,
    )


# --- queue info ----------------------------------------------------------------


def test_queue_info_for_queued_task_uses_expiry_setting() -> None:
    task = _task("q", "queued", execution_expires_after=3600, service_tier="default")
    now = datetime.fromtimestamp(1721400000 + 1260, tz=UTC)
    info = queue_info(task, now=now)
    assert info is not None
    assert info.queued_seconds == 1260
    assert info.expires_at == datetime.fromtimestamp(1721403600, tz=UTC).isoformat()
    assert info.expires_at_estimated is False
    assert info.hint is not None and info.hint.startswith("Queued 21m on default tier")


def test_queue_info_assumes_default_expiry_when_missing() -> None:
    info = queue_info(_task("q", "queued"))
    assert info is not None
    assert info.expires_at_estimated is True
    assert info.expires_at == datetime.fromtimestamp(1721400000 + 172800, tz=UTC).isoformat()


def test_queue_info_running_task() -> None:
    now = datetime.fromtimestamp(1721400100 + 90, tz=UTC)
    info = queue_info(_task("r", "running"), now=now)
    assert info is not None
    assert (info.queued_seconds, info.running_seconds) == (100, 90)


def test_queue_info_none_when_finished() -> None:
    assert queue_info(_task("s", "succeeded")) is None


async def test_batch_unknown_extension_is_a_per_item_error(
    test_env: None, fake_ctx: FakeContext, tmp_path: Path
) -> None:
    odd = tmp_path / "clip.unknownext"
    odd.write_bytes(b"x")
    gw = _gateway()
    with patch("ark_mcp.tools.media_upload_batch.make_object_storage_gateway", return_value=gw):
        result = await media_upload_batch(
            MediaUploadBatchInput(
                items=[
                    {"media_type": "video", "file_path": str(odd)},
                    {
                        "media_type": "image",
                        "mime_type": "image/png",
                        "data": base64.b64encode(b"png").decode(),
                    },
                ]
            ),
            fake_ctx,
        )
    assert isinstance(result, MediaUploadBatchOutput)
    assert result.items[0].error is not None and "infer mime_type" in result.items[0].error
    assert result.succeeded == 1


async def test_batch_cap_rejects_before_decoding_every_item(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap must stop the walk, not be checked after everything is decoded."""
    monkeypatch.setenv("MEDIA_UPLOAD_BATCH_MAX_BYTES", "8")
    get_settings.cache_clear()
    payload = base64.b64encode(b"0123456789").decode()
    gw = _gateway()
    with (
        patch("ark_mcp.tools.media_upload_batch.make_object_storage_gateway", return_value=gw),
        pytest.raises(ValueError, match="at item 0"),
    ):
        await media_upload_batch(
            MediaUploadBatchInput(
                items=[
                    {"media_type": "image", "mime_type": "image/png", "data": payload},
                    {"media_type": "image", "mime_type": "image/png", "data": payload},
                ]
            ),
            fake_ctx,
        )
