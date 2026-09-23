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

from typing import Any, ClassVar

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import Field, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.domain.artifacts import MediaType
from ark_mcp.domain.media import MediaSource, MediaSourceKind
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

_MAX_AUDIOS = 8


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
        max_length=_MAX_AUDIOS,
        description=(
            f"Audio clips to understand (1-{_MAX_AUDIOS}), sent to the model in order. "
            "Use kind='url' with an HTTPS URL (any format the model can decode), or "
            "kind='base64' with raw Base64 data (no data: prefix, at most 10 MB decoded) and "
            "mime_type one of audio/wav, audio/mpeg, audio/flac, audio/aac, or audio/mp4 (m4a). "
            "For local files, upload via media_upload first."
        ),
    )


class SeedAudioUnderstandOutput(SeedUnderstandOutput):
    """Output model for ``seed_audio_understand``."""


async def seed_audio_understand(
    input: SeedAudioUnderstandInput, ctx: Context
) -> SeedAudioUnderstandOutput | ToolResult:
    """Understand, transcribe, or reason about audio clips through a Seed multimodal model.

    Accepts a natural-language prompt plus one or more audio clips (speech,
    music, or sound) and returns only the model's final answer. Use it for
    transcription, translation, speaker or emotion analysis, summaries, meeting
    minutes, or questions about what is heard. The model is set by the server's
    SEED_AUDIO_UNDERSTANDING_MODEL (default 'seed-2-0-lite-260428'). Deep
    thinking is always on (depth set by reasoning_effort) and its trace is never
    returned. Use response_format with 'json_schema' for reliably structured
    output (parsed result in choices[].parsed); 'json_object' is not enforced by
    the default audio model, so use 'json_schema' (optionally with json_retry)
    when JSON is required. Use save_to to write the answer to a local file. For
    local audio files, upload them first with media_upload to obtain an HTTPS
    URL, or pass small wav/mp3/flac/aac/m4a clips as raw Base64. For
    plain long-form transcription with word timings, speech_to_text is the
    dedicated ASR tool. Requires task-augmented execution (or ark_job_submit).
    """
    await context_log(ctx, "info", "Starting Seed audio understanding")
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    if not settings.has_understanding:
        raise ValueError(
            "BYTEPLUS_MODELARK_API_KEY is not configured. Set it in .env to enable understanding tools."
        )

    return await run_understanding(
        input,
        ctx,
        settings=settings,
        model_id=settings.seed_audio_understanding_model,
        supports_thinking=True,
        output_cls=SeedAudioUnderstandOutput,
        audio_parts=[audio.provider_part() for audio in input.audios],
    )


# save_to writes local files, so the tool is not read-only.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
