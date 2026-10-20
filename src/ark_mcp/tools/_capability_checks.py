"""Model-dependent request validation driven by the capability registry.

Pydantic input models enforce rules that hold for every model. These pure
functions check a request against one resolved model's capabilities and
return every violation at once, so a caller fixes a request in one round
trip instead of discovering limits one rejection at a time.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ark_mcp.config.model_capabilities import ImageCapabilities, VideoCapabilities


class VideoRequest(Protocol):
    """Fields of a Seedance create request that depend on the model."""

    @property
    def resolution(self) -> str | None: ...

    @property
    def ratio(self) -> str | None: ...

    @property
    def duration(self) -> int | None: ...

    @property
    def priority(self) -> int | None: ...

    @property
    def execution_expires_after(self) -> int | None: ...

    @property
    def images(self) -> Sequence[object] | None: ...

    @property
    def videos(self) -> Sequence[object] | None: ...

    @property
    def audios(self) -> Sequence[object] | None: ...

    @property
    def draft(self) -> bool | None: ...

    @property
    def draft_task_id(self) -> str | None: ...

    @property
    def omni_reference_task_type(self) -> str | None: ...

    @property
    def output_format(self) -> str | None: ...


def check_video_request(request: VideoRequest, caps: VideoCapabilities) -> list[str]:
    """Return every way the request exceeds the video model's capabilities."""
    violations: list[str] = []
    if (
        request.resolution
        and not request.draft
        and request.resolution not in caps.supported_resolutions
    ):
        violations.append(
            f"resolution '{request.resolution}' is not supported; allowed: "
            f"{list(caps.supported_resolutions)}"
        )
    if request.ratio and request.ratio not in caps.supported_ratios:
        violations.append(
            f"ratio '{request.ratio}' is not supported; allowed: {list(caps.supported_ratios)}"
        )
    low, high = caps.duration_range
    if request.duration not in (None, -1) and not low <= request.duration <= high:
        violations.append(
            f"duration {request.duration}s is outside [{low}, {high}]; use -1 for auto"
        )
    for label, value, (low, high) in (
        ("priority", request.priority, caps.priority_range),
        (
            "execution_expires_after",
            request.execution_expires_after,
            caps.execution_expires_after_range,
        ),
    ):
        if value is not None and not low <= value <= high:
            violations.append(f"{label} {value} is outside [{low}, {high}]")
    for label, items, limit in (
        ("reference images", request.images, caps.max_reference_images),
        ("reference videos", request.videos, caps.max_reference_videos),
        ("reference audios", request.audios, caps.max_reference_audios),
    ):
        if items and len(items) > limit:
            violations.append(f"{len(items)} {label} exceed the maximum of {limit}")
    if (request.draft or request.draft_task_id) and not caps.supports_draft:
        violations.append("Draft mode (draft, draft_task_id) is not supported")
    audio_only = bool(request.audios) and not (request.images or request.videos)
    if audio_only and not caps.supports_audio_only_input:
        violations.append("audio cannot be the only media input; add an image or video")
    if request.omni_reference_task_type and (
        request.omni_reference_task_type not in caps.supported_task_types
    ):
        violations.append(
            "omni_reference_task_type is not supported; allowed: "
            f"{list(caps.supported_task_types) or 'none'}"
        )
    if request.output_format and request.output_format not in caps.supported_output_formats:
        violations.append(
            f"output_format '{request.output_format}' is not supported; allowed: "
            f"{list(caps.supported_output_formats) or 'none'}"
        )
    return violations


class ImageRequest(Protocol):
    """Fields of a Seedream request that depend on the model."""

    @property
    def images(self) -> Sequence[object] | None: ...

    @property
    def size(self) -> str | None: ...

    @property
    def output_format(self) -> str | None: ...

    @property
    def max_images(self) -> int | None: ...

    @property
    def background(self) -> str | None: ...

    @property
    def prompt_optimization(self) -> str | None: ...


def check_image_request(request: ImageRequest, caps: ImageCapabilities) -> list[str]:
    """Return every way the request exceeds the image model's capabilities."""
    violations: list[str] = []
    image_count = len(request.images or [])
    if image_count > caps.max_references:
        violations.append(
            f"{image_count} reference images exceed the maximum of {caps.max_references}"
        )
    if request.output_format and request.output_format not in caps.supported_output_formats:
        violations.append(
            f"output_format '{request.output_format}' is not supported; allowed: "
            f"{list(caps.supported_output_formats)}"
        )
    if (
        caps.supported_sizes is not None
        and request.size
        and request.size not in caps.supported_sizes
    ):
        violations.append(
            f"size '{request.size}' is not supported; allowed: {list(caps.supported_sizes)}"
        )
    if (request.max_images or 1) > 1 and not caps.supports_batch:
        violations.append("max_images > 1 (batch generation) is not supported; use variations")
    if request.prompt_optimization and not caps.supports_prompt_optimization:
        violations.append("prompt_optimization is not supported")
    if request.background == "transparent":
        if not caps.supports_transparent_background:
            violations.append("background='transparent' is not supported")
        if image_count != 1:
            violations.append(
                "background='transparent' needs exactly one input image with an alpha channel"
            )
        if request.output_format not in (None, "png"):
            violations.append("background='transparent' requires output_format omitted or 'png'")
        if any(_is_non_png_base64(image) for image in request.images or []):
            violations.append("background='transparent' needs a PNG input image (image/png)")
    return violations


def _is_non_png_base64(image: object) -> bool:
    return (
        getattr(image, "kind", None) == "base64"
        and getattr(image, "mime_type", None) != "image/png"
    )


def raise_on_violations(violations: list[str], *, model_id: str, family: str, kind: str) -> None:
    """Raise one error listing every violation and where to look up the limits."""
    if not violations:
        return
    bullet_list = "\n".join(f"- {violation}" for violation in violations)
    raise ValueError(
        f"Request is not valid for model '{model_id}' ({family}):\n{bullet_list}\n"
        f"Call ark_list_models(kind='{kind}') for per-model limits."
    )
