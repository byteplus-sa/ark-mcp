"""Seed TTS speech synthesis through the HTTP JSONL endpoint."""

from __future__ import annotations

import base64
from typing import Literal
from uuid import uuid4

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ark_mcp.artifacts.store import artifact_limit_bytes
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.tts import (
    TTS_MAX_CHARACTERS,
    TtsAlignment,
    TtsOutputFormat,
    TtsPronunciationDictionary,
    TtsVoiceSettings,
)
from ark_mcp.providers.seed_speech.client import SeedSpeechGateway
from ark_mcp.providers.seed_speech.seed_tts import SeedTtsService
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.tools._cost import log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._local_export import local_export, output_path_field, overwrite_field
from ark_mcp.tools._persistence import persist_base64


class SeedTtsGenerateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_id: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description=(
            "Authorized cloned voice ID or Speaker ID from the BytePlus Seed TTS 3.0 Voice List: "
            "https://docs.byteplus.com/en/docs/byteplusvoice/voicelist-3-0. "
            "Example English preset: multi_female_skye_v3_uranus_bigtts."
        ),
    )
    text: str | None = Field(
        None,
        min_length=1,
        max_length=TTS_MAX_CHARACTERS,
        description="Text to speak (1-10000 characters); exactly one of text or ssml is required.",
    )
    ssml: str | None = Field(
        None,
        min_length=1,
        max_length=TTS_MAX_CHARACTERS,
        description="Complete SSML instead of text; local safety cap 10000 characters. Tag support depends on voice/model.",
    )
    model: Literal["dola-seed-tts-3.0"] = Field(
        "dola-seed-tts-3.0", description="Seed TTS 3 model; account and voice must be authorized."
    )
    language_code: str = Field(
        "auto",
        min_length=1,
        max_length=64,
        description="Provider language or dialect code, e.g. en for US English; auto by default.",
    )
    output_format: TtsOutputFormat = Field(
        default_factory=lambda: TtsOutputFormat(format="mp3", sample_rate=None, bit_rate=None),
        description="Encoding, sample rate and bitrate.",
    )
    voice_settings: TtsVoiceSettings | None = Field(
        None, description="Optional voice delivery controls."
    )
    enable_subtitle: bool = Field(
        False, description="Return word-level alignment when supported by the language."
    )
    pronunciation_dictionary: TtsPronunciationDictionary | None = Field(
        None,
        description="Optional pronunciation corrections and text replacements before synthesis.",
    )
    session_id: str | None = Field(
        None,
        min_length=1,
        max_length=256,
        description="Reuse only for sequential requests in one conversation; provider history is time/turn limited.",
    )
    text_normalization: bool = Field(True, description="Normalize numbers, dates and similar text.")
    enable_markdown_filter: bool = Field(False, description="Parse Markdown in the input text.")
    silence_duration: float = Field(
        0,
        ge=0,
        le=30000,
        allow_inf_nan=False,
        description="Silence appended after the final sentence, in milliseconds (0-30000).",
    )
    output_path: str | None = output_path_field("the synthesized speech")
    overwrite: bool = overwrite_field()

    @field_validator("text", "ssml", "voice_id", "session_id", "language_code")
    @classmethod
    def validate_nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Value must not be blank.")
        return value

    @model_validator(mode="after")
    def validate_input(self) -> SeedTtsGenerateInput:
        if (self.text is None) == (self.ssml is None):
            raise ValueError("Provide exactly one of text or ssml.")
        if self.voice_id in {".", ".."}:
            raise ValueError("voice_id must not be a relative path segment.")
        return self


class SeedTtsGenerateOutput(BaseModel):
    provider: Literal["byteplus-seed-speech"] = Field(
        "byteplus-seed-speech", description="Speech synthesis provider."
    )
    model: Literal["dola-seed-tts-3.0"] = Field(
        "dola-seed-tts-3.0", description="Model used for speech synthesis."
    )
    artifact: ArtifactRef = Field(
        ..., description="Complete audio, persisted or carrying an explicit storage fallback."
    )
    output_format: TtsOutputFormat = Field(
        ..., description="Resolved audio encoding, sample rate and bitrate."
    )
    session_id: str = Field(
        ..., description="Synthesis session identifier returned by the provider."
    )
    alignments: list[TtsAlignment] = Field(
        default_factory=list,
        description="Subtitle segments in receive order; empty if not returned.",
    )
    request_id: str = Field(..., description="Client-generated X-Request-Id for correlation.")
    provider_log_id: str | None = Field(None, description="Provider X-Tt-Logid, if returned.")


@local_export("artifact")
async def seed_tts_generate(
    input: SeedTtsGenerateInput, ctx: Context
) -> SeedTtsGenerateOutput | ToolResult:
    """Synthesize text or SSML with Seed TTS 3 and an authorized voice_id.

    Submit through ark_job_submit. Collects HTTP audio chunks into one durable artifact,
    not live MCP audio. Returns optional word alignment in seconds. No automatic retries.
    Use seed_audio_generate for full-scene audio; this tool speaks supplied text.
    """
    runtime = get_runtime(ctx)
    settings = runtime.settings
    if not settings.has_seed_audio:
        raise ValueError("Set BYTEPLUS_SEED_SPEECH_API_KEY to enable Seed TTS.")
    output_format = input.output_format.resolved()
    body = input.model_dump(
        exclude={"model", "voice_id", "output_path", "overwrite"}, exclude_none=True
    )
    body["model_id"] = input.model
    body["output_format"] = output_format.model_dump(exclude_none=True)
    request_id = str(uuid4())
    estimated_cost = log_cost_estimate(
        product="tts", variations=1, characters=len(input.text or input.ssml or "")
    )
    service = SeedTtsService(
        SeedSpeechGateway(
            api_key=settings.seed_speech_api_key,
            base_url=settings.seed_audio_base_url,
            timeout=settings.request_timeout_ms / 1000,
            connect_timeout=settings.connect_timeout_ms / 1000,
        ),
        timeout_seconds=settings.request_timeout_ms / 1000,
    )
    await ctx.report_progress(progress=10, total=100)
    try:
        async with billed_provider_slot(
            ctx, provider="seed-speech", product="tts", estimated_cost_usd=estimated_cost
        ):
            response = await service.synthesize(
                body,
                voice_id=input.voice_id,
                request_id=request_id,
                max_audio_bytes=artifact_limit_bytes(MediaType.AUDIO),
            )
    except ProviderError as exc:
        return provider_error_result(exc)
    finally:
        await service.close()
    await ctx.report_progress(progress=80, total=100)
    artifact = await persist_base64(
        runtime.artifact_store,
        data=base64.b64encode(response.audio).decode("ascii"),
        media_type=MediaType.AUDIO,
        mime_type=output_format.mime_type,
        source_expires_at=None,
        auth=get_principal(ctx),
    )
    await ctx.report_progress(progress=100, total=100)
    return SeedTtsGenerateOutput(
        artifact=artifact,
        output_format=output_format,
        session_id=response.session_id,
        alignments=response.alignments,
        request_id=request_id,
        provider_log_id=response.provider_log_id,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
