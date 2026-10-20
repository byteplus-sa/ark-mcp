"""``ark_list_models`` tool: discover configured models, their limits, and enabled services."""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from pydantic import BaseModel, Field

from ark_mcp.config.env import Settings, get_settings
from ark_mcp.config.model_capabilities import (
    ImageCapabilities,
    ModelFamily,
    Seed3DCapabilities,
    UnderstandingCapabilities,
    VideoCapabilities,
    get_capability_registry,
)
from ark_mcp.domain.pricing import ModelPricing, PricingStatus
from ark_mcp.providers.modelark.pricing import get_price_book, live_pricing_enabled
from ark_mcp.tools.seed_audio_understand import MAX_AUDIO_INPUTS

ModelKind = Literal["video", "image", "3d", "understanding"]
UnderstandingModality = Literal["image", "video", "audio"]

VIDEO_TOOL = "seedance_create_task"
SEED3D_TOOLS: dict[ModelFamily, str] = {
    ModelFamily.SEED3D_HYPER3D: "hyper3d_create_task",
    ModelFamily.SEED3D_HITEM3D: "hitem3d_create_task",
}
IMAGE_TOOL = "seedream_generate_image"
UNDERSTANDING_TOOL = "seed_understand"
AUDIO_UNDERSTANDING_TOOL = "seed_audio_understand"


class ArkListModelsInput(BaseModel):
    """Input model for ``ark_list_models``."""

    kind: ModelKind | None = Field(
        None,
        description="Filter to one model kind: video, image, 3d, or understanding. Omit to list every configured kind.",
    )


class VideoModelLimits(BaseModel):
    """Per-model limits for a Seedance video model."""

    resolutions: list[str] = Field(..., description="Accepted resolution values.")
    duration_seconds: tuple[int, int] = Field(
        ...,
        description="Inclusive duration range in seconds; -1 (auto) is always accepted as well.",
    )
    ratios: list[str] = Field(..., description="Accepted ratio values.")
    max_reference_images: int = Field(..., description="Maximum reference images per request.")
    max_reference_videos: int = Field(..., description="Maximum reference videos per request.")
    max_reference_audios: int = Field(..., description="Maximum reference audio clips per request.")
    supports_draft: bool = Field(
        ..., description="Whether draft=true and draft_task_id are accepted."
    )
    draft_final_resolution: str | None = Field(
        None,
        description="Resolution a Draft task renders to when finalized; null without Draft support.",
    )
    supports_audio_only_input: bool = Field(
        ..., description="Whether audio may be the only media input."
    )
    task_types: list[str] = Field(
        ...,
        description="Accepted omni_reference_task_type values; empty when the field is unsupported.",
    )
    output_formats: list[str] = Field(
        ..., description="Accepted output_format values; empty when the field is unsupported."
    )


class ImageModelLimits(BaseModel):
    """Per-model limits for a Seedream image model."""

    max_reference_images: int = Field(..., description="Maximum reference images per request.")
    output_formats: list[str] = Field(..., description="Accepted output_format values.")
    supports_batch: bool = Field(..., description="Whether max_images greater than 1 is accepted.")
    supported_sizes: list[str] | None = Field(
        None, description="Allowed size values; null means any size the provider accepts."
    )
    supports_transparent_background: bool = Field(
        ..., description="Whether background='transparent' is accepted."
    )


class Seed3DModelLimits(BaseModel):
    """Per-model limits for a Seed 3D model."""

    supports_text_to_3d: bool = Field(..., description="Whether a text-only prompt is accepted.")
    max_reference_images: int = Field(..., description="Maximum reference images per request.")
    file_formats: list[str] = Field(..., description="Accepted file_format values.")
    supports_seed: bool = Field(..., description="Whether a seed is accepted.")


class UnderstandingModelLimits(BaseModel):
    """Per-model limits for a multimodal understanding model."""

    modalities: list[UnderstandingModality] = Field(
        ..., description="Input media modalities accepted by the named tool for this model."
    )
    max_media_parts: int = Field(..., description="Maximum media inputs per request.")
    supports_thinking: bool = Field(..., description="Whether deep thinking is available.")
    reasoning_efforts: list[str] = Field(..., description="Accepted reasoning_effort values.")
    response_formats: list[str] = Field(..., description="Accepted response_format types.")


class ModelInfo(BaseModel):
    """One configured model binding."""

    model_id: str = Field(
        ..., description="Configured model ID; pass as `model` when the tool supports selection."
    )
    family: str = Field(..., description="Logical model family.")
    kind: ModelKind = Field(..., description="Model kind.")
    is_default: bool = Field(
        ...,
        description=(
            "Whether this model is used when `model` is omitted. For understanding models, "
            "the default for requests with audio and the default for requests without audio "
            "are both marked."
        ),
    )
    tool: str = Field(
        ..., description="Tool serving this model; understanding limits describe this route."
    )
    video: VideoModelLimits | None = Field(None, description="Limits for video models.")
    image: ImageModelLimits | None = Field(None, description="Limits for image models.")
    seed3d: Seed3DModelLimits | None = Field(None, description="Limits for 3D models.")
    understanding: UnderstandingModelLimits | None = Field(
        None, description="Limits for understanding models."
    )
    pricing: ModelPricing | None = Field(
        None, description="Fresh account catalog prices, or null if not cached for this model."
    )


class ServiceStatus(BaseModel):
    """Whether one optional service is available."""

    service: str = Field(..., description="Service name.")
    enabled: bool = Field(..., description="Whether the service's tools or models are available.")
    reason: str | None = Field(
        None,
        description="Why the service is disabled, naming the env var or flag to set; never a secret value.",
    )


class ArkListModelsOutput(BaseModel):
    """Output model for ``ark_list_models``."""

    models: list[ModelInfo] = Field(..., description="Configured models matching the filter.")
    services: list[ServiceStatus] = Field(..., description="Enablement of each optional service.")
    pricing_status: PricingStatus = Field(
        "unavailable", description="live if a fresh catalog snapshot exists; otherwise unavailable."
    )
    pricing_note: str | None = Field(
        None, description="Why cached prices are unavailable, without credential values."
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


async def ark_list_models(input: ArkListModelsInput, ctx: Context) -> ArkListModelsOutput:
    """List configured models, limits, and cached account prices. Makes no provider calls.

    Call before generation or understanding tools: their `model` argument accepts only the
    exact `model_id` values listed, and the server validates requests against these limits.
    `services` shows which optional services are enabled and the setting that enables a
    disabled one.
    """
    settings = get_settings()
    models = [model for model in describe_models(settings) if input.kind in (None, model.kind)]
    book = get_price_book()
    if live_pricing_enabled(settings) and book.is_fresh():
        return ArkListModelsOutput(
            models=[
                model.model_copy(update={"pricing": book.lookup(model.model_id)})
                for model in models
            ],
            services=describe_services(settings),
            pricing_status="live",
        )
    if not settings.has_modelark_openapi:
        note = "Live pricing needs BytePlus AK/SK credentials."
    elif not live_pricing_enabled(settings):
        note = "Live pricing is disabled (BYTEPLUS_MODELARK_PRICING_REFRESH_SECONDS=0)."
    else:
        note = "No fresh catalog snapshot; the background refresher will retry."
    return ArkListModelsOutput(
        models=models, services=describe_services(settings), pricing_note=note
    )


def describe_models(settings: Settings) -> list[ModelInfo]:
    """Describe every configured binding whose service is enabled."""
    if not settings.has_modelark:
        return []
    image_caps, video_caps, understanding_caps, seed3d_caps = (
        get_capability_registry().configured_models()
    )
    models = [_video_info(caps, settings) for caps in video_caps.values()]
    models += [_image_info(caps, settings) for caps in image_caps.values()]
    if settings.has_seed3d:
        models += [_seed3d_info(caps, settings) for caps in seed3d_caps.values()]
    models += [_understanding_info(caps, settings) for caps in understanding_caps.values()]
    return models


def _video_info(caps: VideoCapabilities, settings: Settings) -> ModelInfo:
    return ModelInfo(
        model_id=caps.model_id,
        family=caps.family.value,
        kind="video",
        is_default=caps.model_id == settings.seedance_default_model,
        tool=VIDEO_TOOL,
        video=VideoModelLimits(
            resolutions=list(caps.supported_resolutions),
            duration_seconds=caps.duration_range,
            ratios=list(caps.supported_ratios),
            max_reference_images=caps.max_reference_images,
            max_reference_videos=caps.max_reference_videos,
            max_reference_audios=caps.max_reference_audios,
            supports_draft=caps.supports_draft,
            draft_final_resolution=caps.draft_final_resolution,
            supports_audio_only_input=caps.supports_audio_only_input,
            task_types=list(caps.supported_task_types),
            output_formats=list(caps.supported_output_formats),
        ),
    )


def _image_info(caps: ImageCapabilities, settings: Settings) -> ModelInfo:
    return ModelInfo(
        model_id=caps.model_id,
        family=caps.family.value,
        kind="image",
        is_default=caps.model_id == settings.seedream_default_model,
        tool=IMAGE_TOOL,
        image=ImageModelLimits(
            max_reference_images=caps.max_references,
            output_formats=list(caps.supported_output_formats),
            supports_batch=caps.supports_batch,
            supported_sizes=list(caps.supported_sizes) if caps.supported_sizes else None,
            supports_transparent_background=caps.supports_transparent_background,
        ),
    )


def _seed3d_info(caps: Seed3DCapabilities, settings: Settings) -> ModelInfo:
    defaults = {settings.hyper3d_default_model, settings.hitem3d_default_model}
    return ModelInfo(
        model_id=caps.model_id,
        family=caps.family.value,
        kind="3d",
        is_default=caps.model_id in defaults,
        tool=SEED3D_TOOLS[caps.family],
        seed3d=Seed3DModelLimits(
            supports_text_to_3d=caps.supports_text_to_3d,
            max_reference_images=caps.max_reference_images,
            file_formats=list(caps.supported_file_formats),
            supports_seed=caps.supports_seed,
        ),
    )


def _understanding_info(caps: UnderstandingCapabilities, settings: Settings) -> ModelInfo:
    is_audio_model = caps.model_id == settings.seed_audio_understanding_model
    modalities: list[UnderstandingModality] = []
    if is_audio_model:
        modalities.append("audio")
    else:
        if caps.supports_image:
            modalities.append("image")
        if caps.supports_video:
            modalities.append("video")
    defaults = {settings.seed_understanding_default_model, settings.seed_audio_understanding_model}
    return ModelInfo(
        model_id=caps.model_id,
        family=caps.family.value,
        kind="understanding",
        is_default=caps.model_id in defaults,
        tool=AUDIO_UNDERSTANDING_TOOL if is_audio_model else UNDERSTANDING_TOOL,
        understanding=UnderstandingModelLimits(
            modalities=modalities,
            max_media_parts=MAX_AUDIO_INPUTS if is_audio_model else caps.max_media_parts,
            supports_thinking=caps.supports_thinking,
            reasoning_efforts=list(caps.reasoning_efforts),
            response_formats=list(caps.response_formats),
        ),
    )


def describe_services(settings: Settings) -> list[ServiceStatus]:
    """Report each optional service and the setting that enables it."""
    storage_vars = (
        "S3_ACCESS_KEY, S3_SECRET_KEY and S3_BUCKET"
        if settings.object_storage_backend == "s3"
        else "TOS_ACCESS_KEY, TOS_SECRET_KEY and TOS_BUCKET"
    )
    services = (
        ("modelark", settings.has_modelark, "Set BYTEPLUS_MODELARK_API_KEY."),
        (
            "seedance_2_5_premium",
            settings.has_seedance_2_5_premium,
            "Set BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED=true (whitelisted accounts only) "
            "and BYTEPLUS_MODELARK_API_KEY.",
        ),
        (
            "seed3d",
            settings.has_seed3d,
            "Set BYTEPLUS_MODELARK_3D_ENABLED=true and BYTEPLUS_MODELARK_API_KEY.",
        ),
        ("seed_audio", settings.has_seed_audio, "Set BYTEPLUS_SEED_SPEECH_API_KEY."),
        ("speech_to_text", settings.has_stt, "Set BYTEPLUS_SEED_SPEECH_API_KEY."),
        ("vod_mediakit", settings.has_vod_mediakit, "Set BYTEPLUS_VOD_MEDIAKIT_API_KEY."),
        (
            "omnihuman",
            settings.has_omnihuman,
            "Set BYTEPLUS_OMNIHUMAN_ENABLED=true and BYTEPLUS_CV_API_KEY.",
        ),
        ("object_storage", settings.has_object_storage, f"Set {storage_vars}."),
        (
            "asset_library",
            settings.has_modelark_openapi,
            "Set BYTEPLUS_MODELARK_ACCESS_KEY and BYTEPLUS_MODELARK_SECRET_KEY.",
        ),
        (
            "asset_delete",
            settings.has_modelark_openapi and settings.modelark_assets_allow_delete,
            "Set BYTEPLUS_MODELARK_ASSETS_ALLOW_DELETE=true with the asset library configured.",
        ),
    )
    return [
        ServiceStatus(service=name, enabled=enabled, reason=None if enabled else reason)
        for name, enabled, reason in services
    ]
