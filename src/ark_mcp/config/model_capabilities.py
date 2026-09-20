"""Model capability registry.

Maps logical model families to operator-configured model IDs and supported
parameters. The server validates combinations before spending quota and can
be updated without rewriting tool handlers.

Model IDs are configuration, not hard-coded truth — the official pages use
inconsistent 5.0 Lite aliases, and API keys / model activation / endpoint IDs
are region-scoped. The operator binds the account-authorized model ID for
each logical family via environment variables.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ark_mcp.config.env import (
    Seed3DFamily,
    SeedanceFamily,
    SeedreamFamily,
    SeedUnderstandingFamily,
    get_settings,
)


class ModelFamily(StrEnum):
    """Logical product family for capability lookup."""

    SEEDREAM_PRO = "seedream_pro"
    SEEDREAM_LITE = "seedream_lite"
    SEEDREAM_4X = "seedream_4x"
    SEEDANCE_2 = "seedance_2"
    SEEDANCE_2_FAST = "seedance_2_fast"
    SEEDANCE_2_MINI = "seedance_2_mini"
    SEEDANCE_2_5 = "seedance_2_5"
    SEED_2_1_PRO = "seed_2_1_pro"
    SEED_2_1_TURBO = "seed_2_1_turbo"
    SEED3D_HYPER3D = "seed3d_hyper3d"
    SEED3D_HITEM3D = "seed3d_hitem3d"


@dataclass(frozen=True)
class ImageCapabilities:
    """Capabilities for image generation models (Seedream)."""

    family: ModelFamily
    model_id: str
    max_references: int
    supports_batch: bool
    supports_streaming: bool
    supported_output_formats: tuple[str, ...]
    supported_sizes: tuple[str, ...] | None = None  # None = accept any
    supports_watermark: bool = True
    supports_prompt_optimization: bool = True
    default_prompt_optimization_mode: str | None = None

    def resolve_prompt_optimization(self, requested: str | None) -> str | None:
        """Return the effective prompt optimization mode for a request.

        An explicit caller value always wins. When omitted, the family
        default applies: on Seedream 5.0 Pro that is
        ``SEEDREAM_PROMPT_OPTIMIZATION_MODE`` (``standard`` unless the
        operator sets ``fast``). Families without a default send nothing
        and the provider applies its own mode.
        """
        if not self.supports_prompt_optimization:
            return None
        return requested or self.default_prompt_optimization_mode


@dataclass(frozen=True)
class VideoCapabilities:
    """Capabilities for video generation models (Seedance)."""

    family: ModelFamily
    model_id: str
    max_reference_images: int
    max_reference_videos: int
    max_reference_audios: int
    supported_resolutions: tuple[str, ...]
    supports_seed: bool
    supports_camera_fixed: bool
    supports_frames: bool
    supports_service_tier_flex: bool
    duration_range: tuple[int, int]  # inclusive bounds, -1 means auto
    priority_range: tuple[int, int]
    execution_expires_after_range: tuple[int, int]
    supports_generate_audio: bool = True
    supports_return_last_frame: bool = True
    supports_watermark: bool = True
    supports_safety_identifier: bool = True


@dataclass(frozen=True)
class UnderstandingCapabilities:
    """Capabilities for multimodal understanding models (Seed 2.1)."""

    family: ModelFamily
    model_id: str
    context_window_tokens: int = 256_000
    supports_image: bool = True
    supports_video: bool = True
    supports_audio: bool = False
    supports_thinking: bool = True
    max_media_parts: int = 32
    reasoning_efforts: tuple[str, ...] = ("low", "medium", "high")


@dataclass(frozen=True)
class Seed3DCapabilities:
    """Capabilities for 3D generation models (Hyper3D, Hitem3d)."""

    family: ModelFamily
    model_id: str
    supports_text_to_3d: bool
    max_reference_images: int
    supported_file_formats: tuple[str, ...]
    supports_seed: bool
    seed_range: tuple[int, int] = (0, 65535)
    supports_callback_url: bool = True


def _seedream_capabilities() -> dict[str, ImageCapabilities]:
    """Build the Seedream capability registry from configured model IDs."""
    settings = get_settings()
    capabilities: dict[str, ImageCapabilities] = {}
    for binding in settings.seedream_model_bindings:
        if binding.family is SeedreamFamily.PRO:
            caps = ImageCapabilities(
                family=ModelFamily.SEEDREAM_PRO,
                model_id=binding.model_id,
                max_references=10,
                supports_batch=False,
                supports_streaming=False,
                supported_output_formats=("png", "jpeg"),
                default_prompt_optimization_mode=settings.seedream_prompt_optimization_mode,
            )
        elif binding.family is SeedreamFamily.LITE:
            caps = ImageCapabilities(
                family=ModelFamily.SEEDREAM_LITE,
                model_id=binding.model_id,
                max_references=14,
                supports_batch=True,
                supports_streaming=True,
                supported_output_formats=("png", "jpeg"),
            )
        else:
            caps = ImageCapabilities(
                family=ModelFamily.SEEDREAM_4X,
                model_id=binding.model_id,
                max_references=14,
                supports_batch=True,
                supports_streaming=True,
                supported_output_formats=("jpeg",),
            )
        capabilities[binding.model_id] = caps
    return capabilities


def _seedance_capabilities() -> dict[str, VideoCapabilities]:
    """Build the Seedance capability registry from configured model IDs."""
    settings = get_settings()
    capabilities: dict[str, VideoCapabilities] = {}
    for binding in settings.seedance_model_bindings:
        resolutions: tuple[str, ...]
        if binding.family is SeedanceFamily.SEEDANCE_2_5:
            family = ModelFamily.SEEDANCE_2_5
            resolutions = ("480p", "720p", "1080p")
        elif binding.family is SeedanceFamily.MINI:
            family = ModelFamily.SEEDANCE_2_MINI
            resolutions = ("480p", "720p")
        elif binding.family is SeedanceFamily.FAST:
            family = ModelFamily.SEEDANCE_2_FAST
            resolutions = ("480p", "720p")
        else:
            family = ModelFamily.SEEDANCE_2
            resolutions = ("480p", "720p", "1080p", "4k")

        if binding.family is SeedanceFamily.SEEDANCE_2_5:
            capabilities[binding.model_id] = VideoCapabilities(
                family=family,
                model_id=binding.model_id,
                max_reference_images=30,
                max_reference_videos=10,
                max_reference_audios=10,
                supported_resolutions=resolutions,
                supports_seed=False,
                supports_camera_fixed=False,
                supports_frames=False,
                supports_service_tier_flex=False,
                duration_range=(-1, 30),
                priority_range=(0, 9),
                execution_expires_after_range=(3600, 259200),
            )
        else:
            capabilities[binding.model_id] = VideoCapabilities(
                family=family,
                model_id=binding.model_id,
                max_reference_images=9,
                max_reference_videos=3,
                max_reference_audios=3,
                supported_resolutions=resolutions,
                supports_seed=False,
                supports_camera_fixed=False,
                supports_frames=False,
                supports_service_tier_flex=False,
                duration_range=(-1, 15),
                priority_range=(0, 9),
                execution_expires_after_range=(3600, 259200),
            )
    return capabilities


def _seed_understanding_capabilities() -> dict[str, UnderstandingCapabilities]:
    """Build the Seed 2.1 understanding capability registry from configured model IDs."""
    settings = get_settings()
    capabilities: dict[str, UnderstandingCapabilities] = {}
    for binding in settings.seed_understanding_model_bindings:
        if binding.family is SeedUnderstandingFamily.PRO:
            family = ModelFamily.SEED_2_1_PRO
        else:
            family = ModelFamily.SEED_2_1_TURBO
        capabilities[binding.model_id] = UnderstandingCapabilities(
            family=family,
            model_id=binding.model_id,
        )
    return capabilities


def _seed3d_capabilities() -> dict[str, Seed3DCapabilities]:
    """Build the Seed3D capability registry from configured model IDs."""
    settings = get_settings()
    capabilities: dict[str, Seed3DCapabilities] = {}
    for binding in settings.seed3d_model_bindings:
        if binding.family is Seed3DFamily.HYPER3D:
            capabilities[binding.model_id] = Seed3DCapabilities(
                family=ModelFamily.SEED3D_HYPER3D,
                model_id=binding.model_id,
                supports_text_to_3d=True,
                max_reference_images=5,
                supported_file_formats=("glb", "obj", "usdz", "fbx", "stl"),
                supports_seed=True,
                seed_range=(0, 65535),
            )
        else:
            capabilities[binding.model_id] = Seed3DCapabilities(
                family=ModelFamily.SEED3D_HITEM3D,
                model_id=binding.model_id,
                supports_text_to_3d=False,
                max_reference_images=4,
                supported_file_formats=("obj", "glb", "stl", "fbx", "usdz"),
                supports_seed=False,
            )
    return capabilities


class CapabilityRegistry:
    """Registry of model capabilities, keyed by configured model ID."""

    def __init__(self) -> None:
        self._image_caps: dict[str, ImageCapabilities] = _seedream_capabilities()
        self._video_caps: dict[str, VideoCapabilities] = _seedance_capabilities()
        self._understanding_caps: dict[str, UnderstandingCapabilities] = (
            _seed_understanding_capabilities()
        )
        self._seed3d_caps: dict[str, Seed3DCapabilities] = _seed3d_capabilities()

    def get_image_capabilities(self, model_id: str | None = None) -> ImageCapabilities:
        """Return image capabilities for the given model or the default."""
        if model_id is None:
            return self._image_caps[get_settings().seedream_default_model]
        if model_id not in self._image_caps:
            raise ValueError(
                f"Model '{model_id}' is not in the configured capability "
                f"registry. Allowed: {list(self._image_caps.keys())}"
            )
        return self._image_caps[model_id]

    def get_video_capabilities(self, model_id: str | None = None) -> VideoCapabilities:
        """Return video capabilities for the given model or the default."""
        if model_id is None:
            return self._video_caps[get_settings().seedance_default_model]
        if model_id not in self._video_caps:
            raise ValueError(
                f"Model '{model_id}' is not in the configured capability "
                f"registry. Allowed: {list(self._video_caps.keys())}"
            )
        return self._video_caps[model_id]

    def list_image_models(self) -> list[str]:
        """Return all configured image model IDs."""
        return list(self._image_caps.keys())

    def list_video_models(self) -> list[str]:
        """Return all configured video model IDs."""
        return list(self._video_caps.keys())

    def get_seed3d_capabilities(
        self,
        model_id: str | None = None,
        *,
        default_model: str | None = None,
    ) -> Seed3DCapabilities:
        """Return 3D generation capabilities for the given model or the family default."""
        settings = get_settings()
        if model_id is None:
            model_id = default_model or settings.hyper3d_default_model
        if model_id not in self._seed3d_caps:
            raise ValueError(
                f"Model '{model_id}' is not in the configured Seed3D capability "
                f"registry. Allowed: {list(self._seed3d_caps.keys())}"
            )
        return self._seed3d_caps[model_id]

    def list_seed3d_models(self) -> list[str]:
        """Return all configured 3D generation model IDs."""
        return list(self._seed3d_caps.keys())

    def get_understanding_capabilities(
        self, model_id: str | None = None
    ) -> UnderstandingCapabilities:
        """Return understanding capabilities for the given model or the default."""
        if model_id is None:
            return self._understanding_caps[get_settings().seed_understanding_default_model]
        if model_id not in self._understanding_caps:
            raise ValueError(
                f"Model '{model_id}' is not in the configured understanding capability "
                f"registry. Allowed: {list(self._understanding_caps.keys())}"
            )
        return self._understanding_caps[model_id]

    def list_understanding_models(self) -> list[str]:
        """Return all configured understanding model IDs."""
        return list(self._understanding_caps.keys())

    def validate_image_size(self, model_id: str | None, size: str | None) -> str | None:
        """Validate that the size is supported by the model, if sizes are restricted."""
        caps = self.get_image_capabilities(model_id)
        if caps.supported_sizes is None or size is None:
            return size
        if size not in caps.supported_sizes:
            raise ValueError(
                f"Size '{size}' is not supported by model '{caps.model_id}'. "
                f"Supported: {caps.supported_sizes}"
            )
        return size

    def validate_output_format(self, model_id: str | None, output_format: str | None) -> str | None:
        """Validate that the output format is supported by the model."""
        if output_format is None:
            return None
        caps = self.get_image_capabilities(model_id)
        if output_format not in caps.supported_output_formats:
            raise ValueError(
                f"Output format '{output_format}' is not supported by model "
                f"'{caps.model_id}'. Supported: {caps.supported_output_formats}"
            )
        return output_format

    def validate_resolution(self, model_id: str | None, resolution: str | None) -> str | None:
        """Validate that the resolution is supported by the video model."""
        if resolution is None:
            return None
        caps = self.get_video_capabilities(model_id)
        if resolution not in caps.supported_resolutions:
            raise ValueError(
                f"Resolution '{resolution}' is not supported by model "
                f"'{caps.model_id}'. Supported: {caps.supported_resolutions}"
            )
        return resolution

    def validate_duration(self, model_id: str | None, duration: int | None) -> int | None:
        """Validate duration against the model's supported range."""
        if duration is None:
            return None
        caps = self.get_video_capabilities(model_id)
        lo, hi = caps.duration_range
        if duration == -1:
            return duration
        if duration < lo or duration > hi:
            raise ValueError(
                f"Duration {duration}s is outside the supported range "
                f"[{lo}, {hi}] for model '{caps.model_id}'. "
                f"Use -1 for auto-duration."
            )
        return duration


_registry: CapabilityRegistry | None = None


def get_capability_registry() -> CapabilityRegistry:
    """Return the cached capability registry."""
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
    return _registry


def refresh_capability_registry() -> CapabilityRegistry:
    """Force-rebuild the capability registry (e.g. after config change)."""
    global _registry
    _registry = CapabilityRegistry()
    return _registry


def to_dict(obj: Any) -> dict[str, Any]:
    """Serialize a capabilities dataclass to a plain dict for logging."""
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items()}
    return {}
