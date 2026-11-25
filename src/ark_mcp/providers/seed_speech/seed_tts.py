"""Bounded Seed TTS 3 JSONL streaming adapter."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.domain.tts import TtsAlignment, TtsWord
from ark_mcp.providers.seed_speech.client import SeedSpeechGateway

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

MAX_EVENT_BYTES = 8 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024


@dataclass(frozen=True)
class SeedTtsResult:
    audio: bytes
    session_id: str
    alignments: list[TtsAlignment]
    provider_log_id: str | None


def _error(
    code: str,
    message: str,
    request_id: str | None,
    *,
    status: int | None = None,
    ambiguous: bool = True,
    retryable: bool = False,
) -> ProviderError:
    return ProviderError(
        NormalizedProviderError(
            provider="seed-speech",
            operation="synthesize_speech",
            code=code,
            message=message,
            request_id=request_id,
            http_status=status,
            retryable=retryable,
            ambiguous_completion=ambiguous,
        )
    )


def _provider_error(
    body: dict[str, Any], status: int, request_id: str, log_id: str | None
) -> ProviderError:
    error = body.get("error")
    error = error if isinstance(error, dict) else {}
    correlation_id = error.get("request_id")
    correlation_id = correlation_id if isinstance(correlation_id, str) else request_id
    provider_log_id = error.get("logid")
    provider_log_id = provider_log_id if isinstance(provider_log_id, str) else log_id
    code = error.get("code")
    code = code if isinstance(code, str) else "TTS_PROVIDER_ERROR"
    message = "Seed TTS request failed; check the error code and provider request identifiers."
    if provider_log_id:
        message += f" Provider log ID: {provider_log_id[:256]}."
    return _error(
        code,
        message,
        correlation_id,
        status=status,
        ambiguous=status < 400 or status >= 500,
        retryable=status == 429,
    )


async def _events(
    response: httpx.Response, max_audio_bytes: int, request_id: str
) -> AsyncIterator[dict[str, Any]]:
    buffer = bytearray()
    received = 0
    metadata = 0
    wire_limit = ((max_audio_bytes + 2) // 3) * 4 + MAX_METADATA_BYTES
    async for chunk in response.aiter_bytes():
        received += len(chunk)
        if received > wire_limit:
            raise _error(
                "OUTPUT_TOO_LARGE", "TTS response exceeds the wire size limit.", request_id
            )
        buffer.extend(chunk)
        while b"\n" in buffer:
            line, _, remaining = buffer.partition(b"\n")
            buffer = remaining
            event = _parse_event(line, request_id)
            if event is not None:
                if event.get("event") != "audio":
                    metadata += len(line)
                    if metadata > MAX_METADATA_BYTES:
                        raise _error(
                            "OUTPUT_TOO_LARGE", "TTS metadata exceeds the size limit.", request_id
                        )
                yield event
        if len(buffer) > MAX_EVENT_BYTES:
            raise _error("OUTPUT_TOO_LARGE", "TTS event exceeds the size limit.", request_id)
    if buffer.strip():
        event = _parse_event(buffer, request_id)
        if event is not None:
            if event.get("event") != "audio" and metadata + len(buffer) > MAX_METADATA_BYTES:
                raise _error("OUTPUT_TOO_LARGE", "TTS metadata exceeds the size limit.", request_id)
            yield event


def _parse_event(line: bytes | bytearray, request_id: str) -> dict[str, Any] | None:
    if len(line) > MAX_EVENT_BYTES:
        raise _error("OUTPUT_TOO_LARGE", "TTS event exceeds the size limit.", request_id)
    if not line.strip():
        return None
    try:
        value = json.loads(line)
    except (ValueError, UnicodeError, RecursionError):
        raise _error("INVALID_STREAM", "Seed TTS returned invalid JSONL.", request_id) from None
    if not isinstance(value, dict):
        raise _error("INVALID_STREAM", "Seed TTS returned a non-object event.", request_id)
    return value


class SeedTtsService:
    def __init__(
        self, gateway: SeedSpeechGateway | None = None, *, timeout_seconds: float | None = None
    ) -> None:
        self._gateway = gateway or SeedSpeechGateway()
        self._timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else get_settings().request_timeout_ms / 1000
        )

    async def synthesize(
        self,
        body: dict[str, Any],
        *,
        voice_id: str,
        request_id: str,
        max_audio_bytes: int,
    ) -> SeedTtsResult:
        """Collect one complete stream; never retry a submitted synthesis."""
        if not voice_id.strip() or voice_id in {".", ".."}:
            raise ValueError("An authorized voice_id is required.")
        if max_audio_bytes <= 0:
            raise ValueError("max_audio_bytes must be positive.")
        path = f"/api/v3/text-to-speech/{quote(voice_id, safe='')}/streaming"
        response_error: ProviderError | None = None
        try:
            async with asyncio.timeout(self._timeout):
                async with self._gateway.stream_tts(path, body, request_id=request_id) as response:
                    log_id = self._gateway.extract_log_id(response)
                    if response.status_code != 200:
                        response_error = _provider_error(
                            {}, response.status_code, request_id, log_id
                        )
                        error_body = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=4096):
                            error_body.extend(chunk)
                            if len(error_body) > MAX_ERROR_BYTES:
                                break
                        try:
                            parsed = (
                                json.loads(error_body) if len(error_body) <= MAX_ERROR_BYTES else {}
                            )
                        except (ValueError, UnicodeError, RecursionError):
                            parsed = {}
                        raise _provider_error(
                            parsed if isinstance(parsed, dict) else {},
                            response.status_code,
                            request_id,
                            log_id,
                        )
                    return await self._collect(response, max_audio_bytes, request_id, log_id)
        except (TimeoutError, httpx.TransportError, httpx.DecodingError) as exc:
            if response_error is not None:
                raise response_error from None
            if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
                raise _error(
                    "CONNECTION_ERROR",
                    "Could not connect to Seed TTS.",
                    request_id,
                    ambiguous=False,
                    retryable=True,
                ) from None
            if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                raise _error(
                    "TIMEOUT",
                    "Seed TTS timed out; synthesis may have been billed. Do not retry blindly.",
                    request_id,
                ) from None
            raise _error(
                "TRANSPORT_ERROR",
                "Seed TTS disconnected; synthesis may have been billed. Do not retry blindly.",
                request_id,
            ) from None

    async def _collect(
        self, response: httpx.Response, max_audio_bytes: int, request_id: str, log_id: str | None
    ) -> SeedTtsResult:
        audio = bytearray()
        session_id: str | None = None
        alignments: list[TtsAlignment] = []
        async for event in _events(response, max_audio_bytes, request_id):
            kind = event.get("event")
            if kind == "error":
                raise _provider_error(event, response.status_code, request_id, log_id)
            if kind == "ready":
                value = event.get("session_id")
                if (
                    session_id is not None
                    or not isinstance(value, str)
                    or not value
                    or len(value) > 256
                ):
                    raise _error(
                        "INVALID_STREAM", "Seed TTS returned an invalid ready event.", request_id
                    )
                session_id = value
                continue
            if session_id is None:
                raise _error("INVALID_STREAM", "Seed TTS omitted the ready event.", request_id)
            if kind == "audio":
                encoded = event.get("audio")
                if not isinstance(encoded, str):
                    raise _error("INVALID_STREAM", "Seed TTS returned invalid audio.", request_id)
                try:
                    decoded = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error):
                    raise _error(
                        "INVALID_STREAM", "Seed TTS returned invalid Base64 audio.", request_id
                    ) from None
                if len(audio) + len(decoded) > max_audio_bytes:
                    raise _error(
                        "OUTPUT_TOO_LARGE", "TTS audio exceeds the artifact size limit.", request_id
                    )
                audio.extend(decoded)
            elif kind == "alignment":
                try:
                    words = event["words"]
                    if not isinstance(words, list):
                        raise ValueError
                    alignment = TtsAlignment(
                        text=event["text"],
                        words=[
                            TtsWord(
                                word=word["word"],
                                start_time=word["startTime"],
                                end_time=word["endTime"],
                            )
                            for word in words
                        ],
                    )
                except (KeyError, TypeError, ValueError):
                    raise _error(
                        "INVALID_STREAM", "Seed TTS returned invalid word alignment.", request_id
                    ) from None
                alignments.append(alignment)
            elif kind == "final":
                if (
                    event.get("is_final") is not True
                    or event.get("reason") != "TTS_STREAM_FINAL_REASON_CLOSED"
                ):
                    raise _error(
                        "INCOMPLETE_STREAM",
                        "Seed TTS did not report successful completion.",
                        request_id,
                    )
                if not audio:
                    raise _error("EMPTY_AUDIO", "Seed TTS returned no audio.", request_id)
                return SeedTtsResult(bytes(audio), session_id, alignments, log_id)
            else:
                raise _error("INVALID_STREAM", "Seed TTS returned an unknown event.", request_id)
        raise _error(
            "INCOMPLETE_STREAM",
            "Seed TTS ended without a final event; do not retry blindly.",
            request_id,
        )

    async def close(self) -> None:
        await self._gateway.close()
