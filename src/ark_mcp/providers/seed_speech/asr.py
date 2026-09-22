"""Seed Speech ASR adapter — speech-to-text over HTTP submit + query.

Submits audio (base64-encoded bytes or public URL) to Seed Speech ASR,
then polls until the transcription is ready. All polling is hidden from
the caller — one call, one complete result.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any
from uuid import uuid4

import httpx

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.domain.transcription import (
    TranscriptionResult,
    TranscriptionUtterance,
    TranscriptionWord,
)
from ark_mcp.observability.logger import debug as log_debug
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.seed_speech.asr_http import SeedSpeechAsrHttpGateway


class SeedSpeechAsrService:
    """Service layer for Seed Speech ASR (speech-to-text)."""

    def __init__(
        self,
        gateway: SeedSpeechAsrHttpGateway | None = None,
    ) -> None:
        self._gateway = gateway
        self._owns_gateway = gateway is None

    async def transcribe(
        self,
        *,
        audio_bytes: bytes | None = None,
        audio_url: str | None = None,
        audio_format: str = "wav",
        language: str = "en-US",
        enable_punc: bool | None = None,
        enable_itn: bool | None = None,
        request_id: str | None = None,
        poll_interval: float = 3.0,
        poll_max: float = 600.0,
    ) -> tuple[TranscriptionResult, str | None]:
        """Transcribe audio via HTTP submit + query.

        Returns ``(result, log_id)``. Blocks until the transcription is ready
        or ``poll_max`` seconds elapse.

        Args:
            audio_bytes: Raw audio bytes (base64-encoded for submit).
            audio_url: Public URL of the audio file (passed directly).
            audio_format: Audio format string (e.g. "wav", "mp3").
            language: BCP-47 language code.
            enable_punc: Enable punctuation output.
            enable_itn: Enable inverse text normalization.
            request_id: Client request ID reused as the ASR task ID for submit
                and query; a fresh UUID is minted when omitted.
            poll_interval: Seconds between query polls (default 3).
            poll_max: Maximum total seconds to wait (default 600).
        """
        if audio_bytes is None and audio_url is None:
            raise ValueError("Either audio_bytes or audio_url must be provided")
        if audio_bytes is not None and audio_url is not None:
            raise ValueError("Provide audio_bytes or audio_url, not both")

        task_id = request_id or str(uuid4())
        audio_data = base64.b64encode(audio_bytes).decode() if audio_bytes is not None else None
        gateway = self._gateway
        if gateway is None:
            gateway = SeedSpeechAsrHttpGateway()
            self._gateway = gateway

        log_info(
            "asr_transcribe_start",
            task_id=task_id,
            language=language,
            has_url=audio_url is not None,
        )

        try:
            await self._submit(
                gateway,
                audio_data=audio_data,
                audio_url=audio_url,
                audio_format=audio_format,
                language=language,
                enable_punc=enable_punc,
                enable_itn=enable_itn,
                request_id=task_id,
            )
        except ProviderError:
            await self._safe_close(gateway)
            raise

        delay = poll_interval
        deadline = time.monotonic() + poll_max
        sequence = 0

        while time.monotonic() < deadline:
            await asyncio.sleep(delay)
            try:
                response = await self._query(gateway, task_id=task_id, sequence=sequence)
            except ProviderError:
                await self._safe_close(gateway)
                raise
            sequence += 1
            if response is not None:
                log_info(
                    "asr_transcribe_complete",
                    task_id=task_id,
                    polls=sequence,
                )
                return self._map_result(response), None
            log_debug("asr_poll_pending", task_id=task_id, poll=sequence)
            delay = min(delay * 2, 10.0)

        log_warning("asr_transcribe_timeout", task_id=task_id, polls=sequence, poll_max=poll_max)
        raise ProviderError(
            NormalizedProviderError(
                provider="seed-speech",
                operation="query_asr_task",
                http_status=None,
                code="TIMEOUT",
                message=f"ASR polling timed out after {poll_max:.0f}s for task {task_id}",
                request_id=task_id,
                retryable=False,
                ambiguous_completion=False,
            )
        )

    @staticmethod
    def _map_result(response: dict[str, Any]) -> TranscriptionResult:
        """Map ASR query response to domain TranscriptionResult."""
        result = response.get("result", {})
        text = result.get("text", "")
        audio_info = response.get("audio_info", {})
        duration_ms = audio_info.get("duration")
        if duration_ms is None:
            additions = result.get("additions", {})
            if isinstance(additions, dict):
                duration_ms = additions.get("duration")
        if duration_ms is not None:
            try:
                duration_ms = int(duration_ms)
            except (TypeError, ValueError):
                duration_ms = None
        utterances = []
        for u in result.get("utterances", []):
            words = [
                TranscriptionWord(
                    text=w.get("text", ""),
                    confidence=w.get("confidence"),
                    start_time_ms=w.get("start_time"),
                    end_time_ms=w.get("end_time"),
                )
                for w in u.get("words", [])
            ]
            utterances.append(
                TranscriptionUtterance(
                    text=u.get("text", ""),
                    start_time_ms=u.get("start_time"),
                    end_time_ms=u.get("end_time"),
                    words=words,
                )
            )
        return TranscriptionResult(text=text, utterances=utterances, duration_ms=duration_ms)

    async def _submit(
        self,
        gateway: SeedSpeechAsrHttpGateway,
        **kwargs: Any,
    ) -> None:
        """Submit with transport error normalization."""
        try:
            await gateway.submit(**kwargs)
        except httpx.TimeoutException:
            raise SeedSpeechAsrHttpGateway.normalize_timeout("submit_asr") from None
        except httpx.ConnectError as exc:
            raise SeedSpeechAsrHttpGateway.normalize_connection_error("submit_asr", exc) from exc
        except httpx.TransportError as exc:
            raise SeedSpeechAsrHttpGateway.normalize_transport_error("submit_asr", exc) from exc

    async def _query(
        self,
        gateway: SeedSpeechAsrHttpGateway,
        *,
        task_id: str,
        sequence: int,
    ) -> dict[str, Any] | None:
        """Query with transport error normalization."""
        try:
            return await gateway.query(task_id=task_id, sequence=sequence)
        except httpx.TimeoutException:
            raise SeedSpeechAsrHttpGateway.normalize_timeout(
                "query_asr", side_effect=False
            ) from None
        except httpx.ConnectError as exc:
            raise SeedSpeechAsrHttpGateway.normalize_connection_error("query_asr", exc) from exc
        except httpx.TransportError as exc:
            raise SeedSpeechAsrHttpGateway.normalize_transport_error("query_asr", exc) from exc

    async def _safe_close(self, gateway: SeedSpeechAsrHttpGateway) -> None:
        """Close the gateway if this service created it internally."""
        if self._owns_gateway:
            await gateway.close()

    async def close(self) -> None:
        """Close the gateway if it was created internally (not injected)."""
        if self._gateway is not None and self._owns_gateway:
            await self._gateway.close()
            self._gateway = None
