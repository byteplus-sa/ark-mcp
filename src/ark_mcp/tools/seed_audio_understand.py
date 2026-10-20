"""``seed_audio_understand`` tool — audio understanding through a ModelArk chat model.

Sends one or more audio clips plus a prompt to the model configured by
``SEED_AUDIO_UNDERSTANDING_MODEL`` (default ``seed-2-0-lite-260428``) through
Chat Completions ``input_audio`` content parts, and returns only the final
answer. Deep thinking is always on and its trace is discarded. The completion
loop (response_format, json_retry, save_to, return_content) is shared with
``seed_understand`` in ``_understanding_shared``.

Provider wire format, verified against ``seed-2-0-lite-260428`` on 2026-09-23:

- URL: ``{"type": "input_audio", "input_audio": {"url": "<https url>"}}``
- Base64: ``{"type": "input_audio", "input_audio": {"data": "<raw b64>",
  "format": "<wav|mp3|flac|aac|m4a>"}}`` — data URIs are rejected, and
  ``format`` is required. ``ogg``, ``opus``, and ``pcm`` are rejected.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import Field, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.domain.artifacts import MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import MediaSource, MediaSourceKind
from ark_mcp.providers.modelark.files import ModelArkFilesService
from ark_mcp.security.output_paths import validate_output_target
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log
from ark_mcp.tools._understanding_shared import (
    SeedUnderstandOutput,
    UnderstandingOptions,
    run_understanding,
)

# Base64 MIME types mapped to the provider's input_audio ``format`` token.
_BASE64_AUDIO_FORMATS: dict[str, str] = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
    "audio/aac": "aac",
    "audio/x-aac": "aac",
    "audio/mp4": "m4a",
}

MAX_AUDIO_INPUTS = 8
_FILES_API_VIDEO_FPS = 0.2
AudioDelivery = Literal["url", "base64", "files_api_audio", "files_api_video"]


def _normalize_mime(mime_type: str | None) -> str:
    """Lower-case a MIME type and drop parameters, matching ``validate_audio_mime``."""
    return (mime_type or "").lower().split(";")[0].strip()


class UnderstandingAudioInput(MediaSource):
    """Audio input for audio understanding, as an HTTPS URL or Base64 data."""

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.AUDIO

    @model_validator(mode="after")
    def require_base64_format(self) -> UnderstandingAudioInput:
        if self.kind != MediaSourceKind.base64:
            return self
        if (self.data or "").lstrip()[:5].lower() == "data:":
            raise ValueError(
                "Base64 audio must be raw Base64 without a 'data:' URI prefix; "
                "set the format through mime_type instead."
            )
        if _normalize_mime(self.mime_type) not in _BASE64_AUDIO_FORMATS:
            raise ValueError(
                "Base64 audio requires mime_type set to one of "
                f"{sorted(_BASE64_AUDIO_FORMATS)} (wav, mp3, flac, aac, m4a). "
                "For other formats, upload via media_upload and pass a URL."
            )
        return self

    def provider_part(self) -> dict[str, Any]:
        """Return the adapter-level part, adding the provider ``format`` for Base64."""
        if self.kind == MediaSourceKind.url:
            return {"kind": "url", "url": self.url}
        audio_format = _BASE64_AUDIO_FORMATS.get(_normalize_mime(self.mime_type))
        if audio_format is None:  # unreachable: require_base64_format rejects this
            raise ValueError("Base64 audio requires a supported mime_type.")
        return {"kind": "base64", "data": self.data, "format": audio_format}


class SeedAudioUnderstandInput(UnderstandingOptions):
    """Input model for ``seed_audio_understand``."""

    audios: list[UnderstandingAudioInput] = Field(
        ...,
        min_length=1,
        max_length=MAX_AUDIO_INPUTS,
        description=(
            f"Audio clips (1-{MAX_AUDIO_INPUTS}), sent in order. kind='url' (HTTPS) or kind='base64' "
            "(raw data without data: prefix, max 10 MB decoded) with mime_type audio/wav, "
            "audio/mpeg, audio/flac, audio/aac, or audio/mp4. Upload local files with "
            "media_upload first."
        ),
    )
    delivery: Literal["auto", "direct", "files_api"] = Field(
        "auto",
        description=(
            "URL delivery: auto retries rejected audio through Files API (up to 512 MB); "
            "direct returns the rejection; files_api uploads first. Videos use their "
            "soundtrack and sampled frames. Base64 stays inline. Uploads are deleted after use."
        ),
    )


class SeedAudioUnderstandOutput(SeedUnderstandOutput):
    """Output model for ``seed_audio_understand``."""

    audio_delivery: list[AudioDelivery] = Field(
        default_factory=list,
        description="Delivery route per input: url, base64, files_api_audio, or files_api_video.",
    )
    delivery_fallback_reason: str | None = Field(
        None, description="Provider rejection that triggered automatic Files API fallback, if any."
    )


async def seed_audio_understand(
    input: SeedAudioUnderstandInput, ctx: Context
) -> SeedAudioUnderstandOutput | ToolResult:
    """Understand or reason about audio (transcribe, translate, analyze, answer questions).

    Returns only the final answer; the server fixes the model (see ark_list_models).
    For structured output use response_format 'json_schema' ('json_object' is not
    enforced by the default model). For plain transcription with word timings use
    speech_to_text. Oversized URL audio and rejected video inputs can use Files API
    (delivery); video frames also consume tokens. Requires background execution (ark_job_submit).
    """
    await context_log(ctx, "info", "Starting Seed audio understanding")
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    if not settings.has_understanding:
        raise ValueError(
            "BYTEPLUS_MODELARK_API_KEY is not configured. Set it in .env to enable understanding tools."
        )

    await validate_output_target(
        input.save_to, ctx=ctx, settings=settings, kind="file", field="save_to"
    )
    model_id = settings.seed_audio_understanding_model
    delivery: list[AudioDelivery] = [
        "url" if audio.kind == MediaSourceKind.url else "base64" for audio in input.audios
    ]
    has_urls = "url" in delivery
    files = ModelArkFilesService()
    uploaded_ids: list[str] = []
    fallback_reason: str | None = None

    async def upload(index: int, audio: UnderstandingAudioInput) -> dict[str, Any]:
        uploaded = await files.upload_url(
            audio.url or "", video_model=model_id, video_fps=_FILES_API_VIDEO_FPS
        )
        uploaded_ids.append(uploaded.id)
        active = await files.wait_until_active(uploaded)
        delivery[index] = "files_api_video" if active.is_video else "files_api_audio"
        return {"kind": "video_file_id" if active.is_video else "file_id", "file_id": active.id}

    async def files_api_parts() -> list[dict[str, Any]]:
        return [
            await upload(index, audio)
            if audio.kind == MediaSourceKind.url
            else audio.provider_part()
            for index, audio in enumerate(input.audios)
        ]

    async def on_rejection(exc: ProviderError) -> list[dict[str, Any]] | None:
        nonlocal fallback_reason
        if exc.error.http_status != 400 or "audio" not in exc.message.lower():
            return None
        fallback_reason = exc.message
        await context_log(
            ctx, "info", "Direct audio input was rejected; retrying through the Files API"
        )
        return await files_api_parts()

    try:
        if input.delivery == "files_api" and has_urls:
            try:
                audio_parts = await files_api_parts()
            except ProviderError as exc:
                return provider_error_result(exc)
        else:
            audio_parts = [audio.provider_part() for audio in input.audios]

        result = await run_understanding(
            input,
            ctx,
            settings=settings,
            model_id=model_id,
            supports_thinking=True,
            output_cls=SeedAudioUnderstandOutput,
            audio_parts=audio_parts,
            audio_rejection_fallback=(
                on_rejection if input.delivery == "auto" and has_urls else None
            ),
        )
    finally:
        try:
            await asyncio.gather(*(files.delete(file_id) for file_id in uploaded_ids))
        finally:
            await files.close()

    if isinstance(result, SeedAudioUnderstandOutput):
        return result.model_copy(
            update={"audio_delivery": delivery, "delivery_fallback_reason": fallback_reason}
        )
    return result


# save_to writes local files, so the tool is not read-only.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
