"""Seed Audio 1.5 tool models and ModelArk execution path."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal
from urllib.parse import urlsplit

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

from ark_mcp.artifacts.store import MAX_AUDIO_URL_DOWNLOAD_BYTES
from ark_mcp.config.model_capabilities import SEED_AUDIO_1_5_MAX_REFERENCE_AUDIOS
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import AudioReference, ReferenceImageInput, validate_reference_url
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.providers.modelark.seed_audio import SeedAudioModelArkService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.security.media_policy import check_audio_duration_from_base64, check_base64_size
from ark_mcp.tools._cost import log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._persistence import persist_from_url, provider_url_ref
from ark_mcp.tools._task_execution import context_log

if TYPE_CHECKING:
    from ark_mcp.tools.seed_audio_generate import SeedAudioGenerateInput

SeedAudioInclude = Literal[
    "video_url",
    "subtitles.srt",
    "subtitles.jsonl",
    "subtitles_source.srt",
    "subtitles_source.jsonl",
]
SeedAudioDubbingLanguage = Literal[
    "zh",
    "en",
    "ja",
    "ko",
    "de",
    "fr",
    "pt-BR",
    "th",
    "id",
    "vi",
    "ms",
    "fil",
    "it",
    "ru",
    "nl",
    "pl",
    "tr",
    "sv",
    "es-ES",
    "es-MX",
    "ar",
    "pt-PT",
    "fi",
    "da",
    "no",
    "cs",
    "hu",
    "el",
    "ro",
    "hi",
]

_OUTPUT_MIME = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "pcm": "audio/pcm",
    "ogg_opus": "audio/ogg",
}
_TOS_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]*[a-z0-9]")
_INPUT_AUDIO_FORMAT = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
}
_MAX_REQUEST_BYTES = 64 * 1024 * 1024
_MAX_REFERENCE_AUDIO_BYTES = 15 * 1024 * 1024
_INLINE_SEPARATION_HEADERS = {"data:audio/wav;base64", "data:audio/mp3;base64"}


def _validate_inline_audio(data: str, *, max_bytes: int, max_seconds: int, label: str) -> None:
    check_base64_size(data, max_bytes, label=label)
    duration = check_audio_duration_from_base64(data, max_seconds, label=label)
    if duration is not None and duration < 2:
        raise ValueError(f"{label} duration must be at least 2 seconds.")


class SeedAudioModelArkReference(AudioReference):
    def _validate_inline_audio(self, data: str) -> None:
        _validate_inline_audio(
            data, max_bytes=_MAX_REFERENCE_AUDIO_BYTES, max_seconds=30, label="reference audio"
        )


class SeedAudioModelArkImage(ReferenceImageInput):
    IMAGE_MIME_TYPES: ClassVar[frozenset[str]] = ReferenceImageInput.IMAGE_MIME_TYPES | {
        "image/bmp",
        "image/tiff",
        "image/gif",
        "image/heic",
        "image/heif",
    }


MODELARK_AUDIO_REFERENCES = TypeAdapter(
    Annotated[
        list[SeedAudioModelArkReference], Field(max_length=SEED_AUDIO_1_5_MAX_REFERENCE_AUDIOS)
    ]
)


def _validate_https(url: str) -> str:
    validate_reference_url(url, allow_asset=False, log_event="seed_audio_invalid_url")
    return url


class SeedAudioCustomSubtitle(BaseModel):
    """Target-language subtitles provided by the caller for direct dubbing."""

    url: str = Field(description="Public HTTPS URL of a UTF-8 SRT with segments up to 15 seconds.")
    format: Literal["srt"] = Field("srt", description="Format of the supplied subtitle file.")

    _check_url = field_validator("url")(_validate_https)


class SeedAudioGlossaryEntry(BaseModel):
    source: str = Field(min_length=1, description="Source-language term.")
    target: str = Field(min_length=1, description="Preferred target-language translation.")


class SeedAudioDubbingConfig(BaseModel):
    """Controls for translating and dubbing a source video."""

    target_language: SeedAudioDubbingLanguage = Field(description="Target language code.")
    source_language: Literal["zh", "en"] | None = Field(
        None, description="Source language code. Omit to detect it automatically."
    )
    glossaries: list[SeedAudioGlossaryEntry] | None = Field(
        None, description="Preferred term translations. Mutually exclusive with custom_subtitle."
    )
    custom_subtitle: SeedAudioCustomSubtitle | None = Field(
        None, description="Use target-language subtitles directly and skip machine translation."
    )
    dialogue_enhancement: Literal["off", "low", "medium", "high"] | None = Field(
        None, description="Dialogue enhancement level. The provider defaults to off."
    )

    @model_validator(mode="after")
    def validate_subtitles(self) -> SeedAudioDubbingConfig:
        if self.custom_subtitle is not None and self.glossaries is not None:
            raise ValueError("custom_subtitle and glossaries are mutually exclusive.")
        return self


class SeedAudioModelArkInput(BaseModel):
    reference_video: str | None = Field(
        None,
        description="Seed Audio 1.5 only. HTTPS reference video for timing. Trusted local mode also accepts authorized tos://.",
    )
    separate_audio: str | None = Field(
        None,
        description="Seed Audio 1.5 only. HTTPS WAV/MP3 URL or Base64 audio data URI for stem separation (2-360 seconds).",
    )
    dubbing_video: str | None = Field(
        None,
        description="Seed Audio 1.5 only. HTTPS source video for dubbing. Trusted local mode also accepts authorized tos://.",
    )
    dubbing_config: SeedAudioDubbingConfig | None = Field(
        None, description="Seed Audio 1.5 only. Required with dubbing_video."
    )
    include: list[SeedAudioInclude] | None = Field(
        None, max_length=5, description="Seed Audio 1.5 only. Optional video and subtitle outputs."
    )

    @field_validator("reference_video", "dubbing_video")
    @classmethod
    def validate_video(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.startswith("tos://"):
            location = urlsplit(value)
            if (
                not _TOS_BUCKET.fullmatch(location.netloc)
                or not location.path.lstrip("/")
                or location.query
                or location.fragment
            ):
                raise ValueError(
                    "Use a tos://<bucket>/<object> video URI without a query or fragment."
                )
            return value
        return _validate_https(value)

    @field_validator("separate_audio")
    @classmethod
    def validate_separation_audio(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith("data:"):
            return _validate_https(value)
        header, _, data = value.partition(",")
        if header not in _INLINE_SEPARATION_HEADERS or not data:
            raise ValueError("Use a WAV or MP3 Base64 data URI for inline stem separation.")
        if len(value) >= _MAX_REQUEST_BYTES:
            raise ValueError("The inline audio exceeds the request size limit. Use an HTTPS URL.")
        _validate_inline_audio(
            data, max_bytes=_MAX_REQUEST_BYTES * 3 // 4, max_seconds=360, label="separation audio"
        )
        return value


class SeedAudioTrack(ArtifactRef):
    track_type: str | None = Field(
        None, description="Provider track type, such as speaker, bgm, or sfx."
    )
    description: str | None = Field(
        None, description="Provider description of the generated track."
    )


class SeedAudioSubtitleFile(BaseModel):
    format: Literal["srt", "jsonl"] = Field(description="Subtitle file format.")
    url: str = Field(description="Temporary provider URL for the subtitle file.")


class SeedAudioModelArkOutput(BaseModel):
    generation_id: str | None = Field(
        None, description="ModelArk generation ID, for Seed Audio 1.5."
    )
    audio_tracks: list[SeedAudioTrack] | None = Field(
        None, description="All generated Seed Audio 1.5 tracks in provider order."
    )
    video_artifact: ArtifactRef | None = Field(
        None, description="Generated video for Seed Audio 1.5, when requested and returned."
    )
    subtitles: list[SeedAudioSubtitleFile] | None = Field(
        None, description="Requested target or generated subtitle files for Seed Audio 1.5."
    )
    subtitles_source: list[SeedAudioSubtitleFile] | None = Field(
        None, description="Requested source-language subtitle files for Seed Audio 1.5 dubbing."
    )


def validate_modelark_references(input: SeedAudioGenerateInput) -> None:
    for reference in input.audio_references:
        if reference.kind == "speaker" or (reference.url or "").startswith("asset://"):
            raise ValueError("Seed Audio 1.5 audio references require an HTTPS URL or Base64 data.")
        mime = reference.mime_type or "audio/wav"
        if mime.lower().split(";", 1)[0].strip() not in _INPUT_AUDIO_FORMAT:
            raise ValueError("Seed Audio 1.5 audio references support WAV and MP3.")
    if input.image_reference is not None and (input.image_reference.url or "").startswith(
        "asset://"
    ):
        raise ValueError("Seed Audio 1.5 image references require an HTTPS URL or Base64 data.")


def build_modelark_request(input: SeedAudioGenerateInput) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if input.text_prompt is not None:
        content.append({"type": "text", "text": input.text_prompt})
    for url, media, role in (
        (input.dubbing_video, "video_url", "dubbing_video"),
        (input.reference_video, "video_url", "reference_video"),
        (input.separate_audio, "audio_url", "separate_audio"),
    ):
        if url is not None:
            content.append({"type": media, media: {"url": url}, "role": role})
    if (image := input.image_reference) is not None:
        mime = (image.mime_type or "image/png").lower().split(";", 1)[0].strip()
        if mime == "image/jpg":
            mime = "image/jpeg"
        url = image.url or f"data:{mime};base64,{image.data}"
        content.append({"type": "image_url", "image_url": {"url": url}, "role": "reference_image"})
    for reference in input.audio_references:
        mime = (reference.mime_type or "audio/wav").lower().split(";", 1)[0].strip()
        url = reference.url or f"data:audio/{_INPUT_AUDIO_FORMAT[mime]};base64,{reference.data}"
        content.append({"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"})

    request: dict[str, Any] = {"model": input.model, "content": content}
    if input.output is not None:
        if input.output.format is not None:
            request["output_format"] = input.output.format
        controls = input.output.model_dump(
            exclude_none=True, exclude={"format", "subtitle", "subtitle_type"}
        )
        if controls:
            request["audio_config"] = controls
    if input.dubbing_config is not None:
        request["dubbing_config"] = input.dubbing_config.model_dump(exclude_none=True)
    if input.include:
        request["include"] = list(dict.fromkeys(input.include))
    if (
        len(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode())
        > _MAX_REQUEST_BYTES
    ):
        raise ValueError("The request body exceeds 64 MiB. Use HTTPS URLs for large references.")
    return request


async def generate_modelark(
    input: SeedAudioGenerateInput, ctx: Context, *, duration_hint: float
) -> dict[str, Any] | ToolResult:
    runtime = get_runtime(ctx)
    settings = runtime.settings
    if not settings.has_modelark:
        raise ValueError("BYTEPLUS_MODELARK_API_KEY is required for Seed Audio 1.5.")
    owner = get_principal(ctx)
    if not owner.is_local and any(
        url and url.startswith("tos://") for url in (input.reference_video, input.dubbing_video)
    ):
        raise ValueError("Raw TOS video URLs require trusted local mode. Use an HTTPS URL.")
    request = build_modelark_request(input)
    estimated_cost = log_cost_estimate(
        product="audio", variations=1, duration_seconds=duration_hint
    )
    service = SeedAudioModelArkService(
        ModelArkGateway(
            api_key=settings.modelark_api_key,
            base_url=settings.modelark_base_url,
            timeout=settings.request_timeout_ms / 1000,
            connect_timeout=settings.connect_timeout_ms / 1000,
        )
    )
    try:
        await ctx.report_progress(progress=50, total=100)
        async with (
            runtime.provider_limiters.modelark_seed_audio,
            billed_provider_slot(
                ctx, provider="modelark", product="audio", estimated_cost_usd=estimated_cost
            ),
        ):
            response, request_id = await call_with_retry(lambda: service.generate(request))
    except ProviderError as exc:
        await context_log(ctx, "error", "Seed Audio 1.5 generation failed.")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=80, total=100)

    async def artifact(url: str, media_type: MediaType, mime: str) -> ArtifactRef:
        if input.persist:
            return await persist_from_url(
                runtime.artifact_store,
                url=url,
                media_type=media_type,
                mime_type=mime,
                source_expires_at=None,
                auth=owner,
                max_download_bytes=MAX_AUDIO_URL_DOWNLOAD_BYTES
                if media_type == MediaType.AUDIO
                else None,
            )
        return provider_url_ref(
            url=url, media_type=media_type, mime_type=mime, source_expires_at=None
        )

    audio_mime = _OUTPUT_MIME[response.output_format or request.get("output_format", "wav")]
    tracks = []
    for track in response.content.audios:
        ref = await artifact(track.url, MediaType.AUDIO, audio_mime)
        tracks.append(
            SeedAudioTrack(**ref.model_dump(), track_type=track.type, description=track.description)
        )

    includes = input.include or []
    video = (
        await artifact(response.content.video_url, MediaType.VIDEO, "video/mp4")
        if response.content.video_url and "video_url" in includes
        else None
    )
    usage = response.usage
    await ctx.report_progress(progress=100, total=100)
    return {
        "provider": "byteplus-modelark",
        "model": input.model,
        "generation_id": response.id,
        "request_id": request_id,
        "duration_seconds": usage.final_duration_ms / 1000
        if usage.final_duration_ms is not None
        else None,
        "billing_duration_seconds": usage.duration_ms / 1000
        if usage.duration_ms is not None
        else None,
        "audio_tracks": tracks,
        "video_artifact": video,
        "subtitles": [
            SeedAudioSubtitleFile(format=item.format, url=item.url)
            for item in response.content.subtitles
            if f"subtitles.{item.format}" in includes
        ],
        "subtitles_source": [
            SeedAudioSubtitleFile(format=item.format, url=item.url)
            for item in response.content.subtitles_source
            if f"subtitles_source.{item.format}" in includes
        ],
    }
