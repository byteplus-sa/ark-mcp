"""ModelArk Files API adapter — upload by URL, poll until active, delete.

Files uploaded here are referenced from Chat Completions as ``file_id``
(``input_audio.file_id`` or ``video_url.file_id``), which lifts the 25 MiB
URL/Base64 input limit to 512 MB per file.

Wire format, verified live on 2026-09-26 (``seed-2-0-lite-260428``):

- ``POST /files`` accepts only ``multipart/form-data``; a URL upload sends the
  text fields ``purpose`` and ``url``, so ModelArk fetches the file itself and
  this server never downloads caller URLs.
- New files start ``processing`` and become ``active`` within seconds; only
  ``active`` files can be referenced.
- ``input_audio.file_id`` rejects a video container ("type is not audio").
  Videos go through ``video_url.file_id`` and must be uploaded with
  ``preprocess_configs[video][model]`` set to an audio-capable model so the
  soundtrack is processed.

Docs: https://docs.byteplus.com/en/docs/ModelArk/1870399
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.observability.logger import debug as log_debug
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.providers.modelark.seedance import _parse_success_body

if TYPE_CHECKING:
    from collections.abc import Awaitable

# The Files API accepts expire_at in [now + 1 day, now + 30 days]. The margin
# keeps the minimum valid after request latency and clock skew.
MIN_RETENTION_SECONDS = 86_400
MAX_RETENTION_SECONDS = 2_592_000
_RETENTION_MARGIN_SECONDS = 300


class ModelArkFileError(BaseModel):
    """Error details on a ``failed`` file."""

    code: str | None = None
    message: str | None = None


class ModelArkFile(BaseModel):
    """The ModelArk file object (only the fields this server reads)."""

    id: str
    status: str
    bytes: int | None = None
    mime_type: str | None = None
    filename: str | None = None
    expire_at: int | None = None
    error: ModelArkFileError | None = None

    @property
    def is_video(self) -> bool:
        """Whether ModelArk classified the file as a video."""
        return (self.mime_type or "").lower().startswith("video/")


def _file_error(operation: str, message: str, *, code: str, retryable: bool) -> ProviderError:
    return ProviderError(
        NormalizedProviderError(
            provider="modelark",
            operation=operation,
            code=code,
            message=message,
            retryable=retryable,
            ambiguous_completion=False,
        )
    )


class ModelArkFilesService:
    """Service layer for the ModelArk Files API."""

    def __init__(self, gateway: ModelArkGateway | None = None) -> None:
        self._gateway = gateway or ModelArkGateway()

    async def upload_url(
        self,
        url: str,
        *,
        video_model: str | None = None,
        video_fps: float | None = None,
        retention_seconds: int = MIN_RETENTION_SECONDS,
    ) -> ModelArkFile:
        """Ask ModelArk to fetch ``url`` into a new file (``purpose=user_data``).

        ``video_model`` and ``video_fps`` set ``preprocess_configs[video]``;
        they only affect video files. ``video_model`` must be audio-capable for
        a video's soundtrack to be understood; ``video_fps`` (0.2-5) trades
        frame detail for fewer input tokens.
        """
        retention = min(
            max(retention_seconds, MIN_RETENTION_SECONDS) + _RETENTION_MARGIN_SECONDS,
            MAX_RETENTION_SECONDS - _RETENTION_MARGIN_SECONDS,
        )
        fields = {
            "purpose": "user_data",
            "url": url,
            "expire_at": str(int(time.time()) + retention),
        }
        if video_model:
            fields["preprocess_configs[video][model]"] = video_model
        if video_fps is not None:
            fields["preprocess_configs[video][fps]"] = f"{video_fps:g}"
        response = await self._send("upload_file", self._gateway.post_multipart("/files", fields))
        uploaded = ModelArkFile.model_validate(_parse_success_body(response, "upload_file"))
        log_debug("modelark_file_uploaded", file_id=uploaded.id, status=uploaded.status)
        return uploaded

    async def retrieve(self, file_id: str) -> ModelArkFile:
        """Return the current file object."""
        response = await self._send("retrieve_file", self._gateway.get(f"/files/{file_id}"))
        return ModelArkFile.model_validate(_parse_success_body(response, "retrieve_file"))

    async def wait_until_active(
        self,
        file: ModelArkFile,
        *,
        timeout_seconds: float = 600.0,
        poll_seconds: float = 1.0,
    ) -> ModelArkFile:
        """Poll until ``file`` is ``active``; raise ``ProviderError`` on failure or timeout."""
        deadline = time.monotonic() + timeout_seconds
        current = file
        while current.status == "processing":
            if time.monotonic() >= deadline:
                raise _file_error(
                    "retrieve_file",
                    f"ModelArk file {current.id} was still processing after "
                    f"{int(timeout_seconds)} seconds.",
                    code="FILE_PROCESSING_TIMEOUT",
                    retryable=True,
                )
            await asyncio.sleep(poll_seconds)
            current = await self.retrieve(current.id)
        if current.status != "active":
            error = current.error or ModelArkFileError()
            detail = f": {error.message}" if error.message else "."
            raise _file_error(
                "retrieve_file",
                f"ModelArk could not process file {current.id} (status {current.status!r}){detail}",
                code=error.code or "FILE_PROCESSING_FAILED",
                retryable=False,
            )
        return current

    async def delete(self, file_id: str) -> bool:
        """Delete a file. Never raises: a leftover file still expires on its own."""
        try:
            response = await self._gateway.delete(f"/files/{file_id}")
        except httpx.HTTPError as exc:
            log_warning("modelark_file_delete_failed", file_id=file_id, error=type(exc).__name__)
            return False
        if response.status_code >= 400:
            log_warning(
                "modelark_file_delete_failed", file_id=file_id, http_status=response.status_code
            )
            return False
        return True

    async def _send(self, operation: str, call: Awaitable[httpx.Response]) -> httpx.Response:
        try:
            response = await call
        except httpx.TimeoutException:
            raise ModelArkGateway.normalize_timeout(
                operation, side_effect=operation == "upload_file"
            ) from None
        except httpx.ConnectError as exc:
            raise ModelArkGateway.normalize_connection_error(operation, exc) from exc
        except httpx.TransportError as exc:
            error = ModelArkGateway.normalize_transport_error(operation, exc)
            if operation == "upload_file":
                error = ProviderError(
                    error.error.model_copy(
                        update={"ambiguous_completion": True, "retryable": False}
                    )
                )
            raise error from exc
        if response.status_code >= 400:
            raise ModelArkGateway.normalize_error(response, operation)
        return response

    async def close(self) -> None:
        await self._gateway.close()
