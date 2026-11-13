"""``speech_to_text`` tool — transcribe audio via Seed Speech ASR.

Resolves audio to raw bytes (URL → SSRF-safe download, Base64 → decode, file →
read), submits to Seed Speech ASR via HTTP, and polls until complete. Required
MCP task augmentation returns an MCP task ID immediately; clients obtain the
``TranscriptionResult`` from the terminal ``tasks/get`` response. No separate
provider task tool or TOS upload is required — the HTTP submit + poll is contained within
the MCP task.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.artifacts.filesystem_store import _is_trusted_host
from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.transcription import TranscriptionResult
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.providers.seed_speech.asr import SeedSpeechAsrService
from ark_mcp.runtime import billed_provider_slot, get_runtime
from ark_mcp.security.media_policy import decode_base64_safely
from ark_mcp.security.url_policy import UrlValidationError, validate_url
from ark_mcp.tools._cost import log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log

_STT_MAX_BYTES = 200 * 1024 * 1024
_BYTES_PER_SECOND_16KHZ_MONO_16BIT = 32000


def _estimate_duration_seconds(num_bytes: int) -> float:
    return max(num_bytes / _BYTES_PER_SECOND_16KHZ_MONO_16BIT, 1.0)


class AsrAudioInput(BaseModel):
    """Audio source — resolved to raw bytes for HTTP submit."""

    audio_url: str | None = Field(
        None, description="HTTPS URL of the audio (max 200 MB). Exactly one audio source."
    )
    audio_data: str | None = Field(
        None, description="Base64 audio bytes (max 200 MB decoded). Exactly one audio source."
    )
    audio_file_path: str | None = Field(
        None,
        description="Absolute local file path (stdio only, max 200 MB). Exactly one audio source.",
    )
    audio_format: Literal["wav", "mp3", "ogg", "raw", "flac"] = Field(
        ...,
        description="Format of the source audio.",
    )

    @model_validator(mode="after")
    def validate_exactly_one_source(self) -> AsrAudioInput:
        provided = sum(1 for v in (self.audio_url, self.audio_data, self.audio_file_path) if v)
        if provided != 1:
            raise ValueError("Provide exactly one of audio_url, audio_data, or audio_file_path.")
        if self.audio_url:
            try:
                validate_url(self.audio_url)
            except UrlValidationError as exc:
                log_warning("asr_audio_invalid_url", error=str(exc))
                raise ValueError(UrlValidationError.safe_message) from exc
        return self


class AsrRequestOptions(BaseModel):
    """Optional transcription feature toggles."""

    language: str = Field("en-US", description="BCP-47 language code, e.g. en-US.")
    enable_punc: bool | None = Field(None, description="Add punctuation to the text.")
    enable_itn: bool | None = Field(
        None, description="Inverse text normalization (e.g. spoken numbers to digits)."
    )


class SpeechToTextInput(BaseModel):
    """Input for the ``speech_to_text`` tool."""

    audio: AsrAudioInput = Field(..., description="Audio source to transcribe.")
    options: AsrRequestOptions | None = Field(
        None, description="Optional transcription feature toggles."
    )


class SpeechToTextOutput(BaseModel):
    """Output for the ``speech_to_text`` tool."""

    result: TranscriptionResult = Field(
        ..., description="Full transcription result with text, utterances, and timing."
    )
    log_id: str | None = Field(None, description="Provider-side log ID for troubleshooting.")


async def _resolve_audio_bytes(audio: AsrAudioInput, ctx: Context) -> bytes:
    """Resolve any input source to raw audio bytes."""
    if audio.audio_url:
        downloaded = await get_runtime(ctx).safe_downloader.download(
            audio.audio_url,
            trusted_hosts=_is_trusted_host,
            max_bytes=_STT_MAX_BYTES,
        )
        return downloaded.body
    if audio.audio_data:
        return decode_base64_safely(audio.audio_data, _STT_MAX_BYTES, label="audio")
    p = Path(audio.audio_file_path or "").expanduser().resolve()
    settings = get_settings()
    if settings.mcp_transport != "stdio":
        raise ValueError("audio_file_path is only supported in stdio transport mode.")
    if not p.is_file():
        raise ValueError("Audio file not found.")
    if p.stat().st_size > _STT_MAX_BYTES:
        raise ValueError(
            f"Audio file size ({p.stat().st_size} bytes) exceeds limit ({_STT_MAX_BYTES} bytes)."
        )
    return p.read_bytes()


async def speech_to_text(input: SpeechToTextInput, ctx: Context) -> SpeechToTextOutput | ToolResult:
    """Transcribe audio to text with word-level timings via Seed Speech ASR.

    Accepts an HTTPS URL, Base64 data, or a local file path (stdio only), up to
    200 MB. Requires background execution (ark_job_submit).
    """
    await context_log(ctx, "info", "Starting speech-to-text transcription")
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    if not settings.has_stt:
        raise ValueError(
            "BYTEPLUS_SEED_SPEECH_API_KEY is not configured. Set it in .env to enable speech-to-text."
        )

    try:
        audio_bytes = await _resolve_audio_bytes(input.audio, ctx)
    except (ProviderError, ValueError) as exc:
        log_warning("audio_resolution_failed", error=str(exc))
        await context_log(ctx, "error", "Audio resolution failed.")
        if isinstance(exc, ProviderError):
            return provider_error_result(exc)
        detail = "Invalid audio source URL." if isinstance(exc, UrlValidationError) else str(exc)
        return ToolResult(
            content=[{"type": "text", "text": f"Invalid audio input: {detail}"}],
            is_error=True,
        )

    await ctx.report_progress(progress=30, total=100)
    duration_est = _estimate_duration_seconds(len(audio_bytes))
    estimated_cost = log_cost_estimate(product="stt", variations=1, duration_seconds=duration_est)

    options = input.options or AsrRequestOptions()
    service = SeedSpeechAsrService()
    client_request_id = str(uuid4())
    try:
        async with billed_provider_slot(
            ctx,
            provider="seed-speech",
            product="stt",
            estimated_cost_usd=estimated_cost,
        ):
            result, log_id = await call_with_retry(
                lambda: service.transcribe(
                    audio_bytes=audio_bytes,
                    audio_format=input.audio.audio_format,
                    language=options.language,
                    enable_punc=options.enable_punc,
                    enable_itn=options.enable_itn,
                    poll_interval=settings.seed_speech_asr_poll_interval_seconds,
                    poll_max=settings.seed_speech_asr_poll_max_seconds,
                    request_id=client_request_id,
                )
            )
    except ProviderError as exc:
        await context_log(ctx, "error", f"Speech-to-text failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "stt_completed",
        chars=len(result.text),
        utterances=len(result.utterances),
        log_id=log_id,
    )
    return SpeechToTextOutput(result=result, log_id=log_id)


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
