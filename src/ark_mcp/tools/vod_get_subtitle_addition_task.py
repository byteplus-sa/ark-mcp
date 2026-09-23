"""Poll a MediaKit subtitle burn-in task."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field

from ark_mcp.providers.vod_mediakit.subtitles import VodMediaKitSubtitleBurnInService
from ark_mcp.tools._local_export import (
    local_export,
    output_path_field,
    overwrite_field,
)
from ark_mcp.tools._local_export import (
    needs_persist_output as _needs_persist_output,
)
from ark_mcp.tools._task_execution import persistence_requires_task
from ark_mcp.tools._vod_subtitle_shared import (
    VodSubtitleTaskOutput,
    poll_subtitle_task,
)


class VodGetSubtitleAdditionTaskInput(BaseModel):
    """Input for ``vod_get_subtitle_addition_task``."""

    task_id: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Provider task ID from the task-augmented result of vod_add_subtitles.",
    )
    persist_output: bool = Field(
        default=True,
        description=(
            "Whether to copy a completed subtitled video into durable artifact storage on first successful poll. "
            "The default true requires task-augmented execution; use false for a foreground status check."
        ),
    )
    output_path: str | None = output_path_field("the subtitled video")
    overwrite: bool = overwrite_field()


class VodSubtitleAdditionTaskOutput(VodSubtitleTaskOutput):
    """Normalized status and output of a MediaKit subtitle burn-in task."""


@local_export("video", precondition=_needs_persist_output)
async def vod_get_subtitle_addition_task(
    input: VodGetSubtitleAdditionTaskInput, ctx: Context
) -> VodSubtitleAdditionTaskOutput | ToolResult:
    """Poll and retrieve a BytePlus VOD AI MediaKit subtitle burn-in task.

    On the first successful poll with persist_output enabled, copies the
    temporary MP4 output into durable artifact storage with single-flight
    concurrency protection. Provider success remains visible if persistence
    is skipped or fails. Supports optional MCP task-augmented execution for
    completed-output persistence.
    """
    task_error = persistence_requires_task(ctx, input.persist_output)
    if task_error is not None:
        return task_error
    return await poll_subtitle_task(
        input_task_id=input.task_id,
        persist_output=input.persist_output,
        ctx=ctx,
        service=VodMediaKitSubtitleBurnInService(),
        output_type=VodSubtitleAdditionTaskOutput,
        label="subtitle burn-in",
        log_event="vod_get_subtitle_addition_task_complete",
    )


# Not read-only: output_path/output_dir can write local files.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
