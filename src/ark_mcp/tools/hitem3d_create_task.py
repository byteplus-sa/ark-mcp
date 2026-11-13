"""``hitem3d_create_task`` tool — create an asynchronous Hitem3d 3D task.

Hitem3d (Hitem3d-2.0) is image-to-3D only (1-4 images). Model parameters
are passed as ``--<param> <value>`` commands in the text content item.
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

_HITEM3D_FILE_FORMATS: dict[str, int] = {
    "obj": 1,
    "glb": 2,
    "stl": 3,
    "fbx": 4,
    "usdz": 5,
}


class Hitem3dCreateTaskInput(BaseModel):
    """Input model for ``hitem3d_create_task``."""

    images: list[Seed3DImageInput] = Field(
        ...,
        min_length=1,
        max_length=4,
        description="Reference images (1-4).",
    )
    model: str | None = Field(
        None,
        description="Hitem3d model ID from ark_list_models(kind='3d'). Omit for the default.",
    )
    callback_url: str | None = Field(
        None,
        description="Callback URL notified on task status changes.",
    )
    resolution: Literal["1536", "1536pro"] | None = Field(
        None,
        description="Resolution. Default 1536.",
    )
    face: int | None = Field(
        None,
        ge=100000,
        le=2000000,
        description="Face count.",
    )
    file_format: Literal["obj", "glb", "stl", "fbx", "usdz"] | None = Field(
        None,
        description="Output format. Default obj.",
    )
    request_type: Literal[1, 3] | None = Field(
        None,
        description="1 = geometry only, 3 = geometry + texture (default).",
    )
    multi_images_bit: str | None = Field(
        None,
        max_length=4,
        description="Bitmap of views present, order front/back/left/right ('1010' = front + left).",
    )

    @model_validator(mode="after")
    def validate_multi_images_bit(self) -> Hitem3dCreateTaskInput:
        if self.multi_images_bit is not None and not all(
            ch in "01" for ch in self.multi_images_bit
        ):
            raise ValueError("multi_images_bit must contain only 0 and 1.")
        return self

    def command_params(self) -> dict[str, object]:
        """Collect model text-command parameters for the provider request."""
        return {
            "resolution": self.resolution,
            "face": self.face,
            "fileformat": (_HITEM3D_FILE_FORMATS[self.file_format] if self.file_format else None),
            "request_type": self.request_type,
            "multi_images_bit": self.multi_images_bit,
        }


async def hitem3d_create_task(
    input: Hitem3dCreateTaskInput, ctx: Context
) -> Seed3DCreateTaskOutput | ToolResult:
    """Create a Hitem3d image-to-3D generation task (1-4 images).

    Poll the returned task_id with gen_task_get. Requires background execution (ark_job_submit).
    """
    await context_log(ctx, "info", "Creating Hitem3d 3D generation task")
    settings = get_settings()
    if not settings.has_seed3d:
        raise ValueError(
            "BYTEPLUS_MODELARK_3D_ENABLED is not set. Enable it in .env to use Hitem3d tools."
        )

    registry = get_capability_registry()
    caps = registry.get_seed3d_capabilities(
        input.model, default_model=settings.hitem3d_default_model
    )

    if caps.family is not ModelFamily.SEED3D_HITEM3D:
        raise ValueError(
            f"Model '{caps.model_id}' is not a Hitem3d model. "
            f"Use hyper3d_create_task for Hyper3D models."
        )

    result = await execute_seed3d_create(
        ctx=ctx,
        caps=caps,
        prompt=None,
        images=input.images,
        seed=None,
        callback_url=input.callback_url,
        command_params=input.command_params(),
    )
    if isinstance(result, ToolResult):
        return result

    task_id, _request_id = result
    log_info(
        "hitem3d_create_task_complete",
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
