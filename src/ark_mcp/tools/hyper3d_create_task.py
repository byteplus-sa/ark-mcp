"""``hyper3d_create_task`` tool — create an asynchronous Hyper3D 3D task.

Hyper3D (Hyper3d-Gen2) supports text-to-3D and image-to-3D (1-5 images).
Model parameters are passed as ``--<param> <value>`` commands appended to
the text content item.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import ModelFamily, get_capability_registry
from ark_mcp.observability.logger import info as log_info
from ark_mcp.tools._seed3d_shared import (
    Seed3DCreateTaskOutput,
    Seed3DImageInput,
    execute_seed3d_create,
)
from ark_mcp.tools._task_execution import context_log


class Hyper3DCreateTaskInput(BaseModel):
    """Input model for ``hyper3d_create_task``."""

    prompt: str | None = Field(
        None,
        max_length=400,
        description="English description of the model (max 400 chars). Required when no images are given.",
    )
    images: list[Seed3DImageInput] | None = Field(
        None,
        max_length=5,
        description="Reference images for image-to-3D (max 5); a prompt may accompany them.",
    )
    model: str | None = Field(
        None,
        description="Hyper3D model ID from ark_list_models(kind='3d'). Omit for the default.",
    )
    seed: int | None = Field(
        None,
        ge=0,
        le=65535,
        description="Random seed for reproducible output.",
    )
    callback_url: str | None = Field(
        None,
        description="Callback URL notified on task status changes.",
    )
    material: Literal["PBR", "Shaded", "All", "None"] | None = Field(
        None,
        description="Material type; None gives a white model. Default PBR.",
    )
    mesh_mode: Literal["Raw", "Quad"] | None = Field(
        None,
        description="Raw (triangles) or Quad. Default Quad.",
    )
    quality_override: int | None = Field(
        None,
        description="Polygon face count (Raw: 500-1000000, Quad: 1000-200000).",
    )
    addons: Literal["HighPack"] | None = Field(
        None,
        description="HighPack gives 4K textures.",
    )
    use_original_alpha: bool | None = Field(
        None,
        description="Preserve transparent areas of the input image.",
    )
    bbox_condition: list[int] | None = Field(
        None,
        max_length=3,
        min_length=3,
        description="Bounding box [width, height, length].",
    )
    ta_pose: bool | None = Field(
        None,
        description="Force a T-Pose/A-Pose bind for humanoid models.",
    )
    subdivision_level: Literal["high", "medium", "low"] | None = Field(
        None,
        description="Polygon count level; ignored when quality_override is set.",
    )
    file_format: Literal["glb", "obj", "usdz", "fbx", "stl"] | None = Field(
        None,
        description="Output format. Default glb.",
    )
    hd_texture: bool | None = Field(
        None,
        description="Enable HD textures.",
    )

    @model_validator(mode="after")
    def validate_content_required(self) -> Hyper3DCreateTaskInput:
        """Text-to-3D requires a prompt; otherwise at least one input is required."""
        if not self.images and not self.prompt:
            raise ValueError("At least one of prompt or images is required for Hyper3D generation.")
        return self

    def command_params(self) -> dict[str, object]:
        """Collect model text-command parameters for the provider request."""
        return {
            "material": self.material,
            "mesh_mode": self.mesh_mode,
            "quality_override": self.quality_override,
            "addons": self.addons,
            "use_original_alpha": self.use_original_alpha,
            "bbox_condition": self.bbox_condition,
            "TAPose": self.ta_pose,
            "subdivisionlevel": self.subdivision_level,
            "fileformat": self.file_format,
            "hd_texture": self.hd_texture,
        }


async def hyper3d_create_task(
    input: Hyper3DCreateTaskInput, ctx: Context
) -> Seed3DCreateTaskOutput | ToolResult:
    """Create a Hyper3D 3D generation task from a text prompt and/or up to 5 images.

    Poll the returned task_id with gen_task_get. Requires background execution (ark_job_submit).
    """
    await context_log(ctx, "info", "Creating Hyper3D 3D generation task")
    settings = get_settings()
    if not settings.has_seed3d:
        raise ValueError(
            "BYTEPLUS_MODELARK_3D_ENABLED is not set. Enable it in .env to use Hyper3D tools."
        )

    registry = get_capability_registry()
    caps = registry.get_seed3d_capabilities(
        input.model, default_model=settings.hyper3d_default_model
    )

    if caps.family is not ModelFamily.SEED3D_HYPER3D:
        raise ValueError(
            f"Model '{caps.model_id}' is not a Hyper3D model. "
            f"Use hitem3d_create_task for Hitem3d models."
        )

    if not caps.supports_text_to_3d and not input.images:
        raise ValueError(f"Model '{caps.model_id}' requires at least one image.")

    result = await execute_seed3d_create(
        ctx=ctx,
        caps=caps,
        prompt=input.prompt,
        images=input.images,
        seed=input.seed,
        callback_url=input.callback_url,
        command_params=input.command_params(),
    )
    if isinstance(result, ToolResult):
        return result

    task_id, _request_id = result
    log_info(
        "hyper3d_create_task_complete",
        task_id=task_id,
        model=caps.model_id,
    )
    return Seed3DCreateTaskOutput(
        task_id=task_id,
        status="queued",
        recommended_poll_after_ms=5000,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
