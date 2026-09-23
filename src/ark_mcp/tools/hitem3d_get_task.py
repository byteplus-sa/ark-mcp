"""``hitem3d_get_task`` tool — retrieve the status and output of a Hitem3d task.

On first successful retrieval, copies the 24-hour ``file_url`` (zip package
of the 3D file) into the durable artifact store.
"""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.tools._local_export import local_export
from ark_mcp.tools._local_export import needs_persist_output as _needs_persist_output
from ark_mcp.tools._seed3d_shared import (
    Seed3DGetTaskInput,
    Seed3DTaskOutput,
    seed3d_get_task_impl,
)


@local_export("file", precondition=_needs_persist_output)
async def hitem3d_get_task(
    input: Seed3DGetTaskInput, ctx: Context
) -> Seed3DTaskOutput | ToolResult:
    """Retrieve the status and output of a Hitem3d 3D generation task.

    On first successful retrieval with ``persist_output=True``, copies the
    provider's 24-hour zip URL into durable artifact storage so the
    ``seed-media://`` resource remains available after expiry. Supports optional
    MCP task-augmented execution for completed-output persistence.
    """
    return await seed3d_get_task_impl(input, ctx, "hitem3d")


# Not read-only: output_path/output_dir can write local files.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
