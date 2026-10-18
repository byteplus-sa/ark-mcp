"""``vod_get_task`` tool — poll any BytePlus VOD AI MediaKit task.

One poller for every ``vod_*`` create tool: the caller passes the
``operation`` the create tool returned. On the first successful poll of a
completed task with ``persist_output=True``, each expiring provider output URL
is copied into ``ArtifactStore`` once per task and cached so repeated polls
do not download again.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.artifacts.export import LocalExportTarget, export_ref, prepare_local_export
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.models import TaskError, TaskOutputFile, VodTaskStatus
from ark_mcp.observability.logger import info as log_info
from ark_mcp.runtime import RuntimeServices, get_principal, get_runtime
from ark_mcp.security.auth_context import PrincipalContext
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._local_export import needs_persist_output, output_path_field, overwrite_field
from ark_mcp.tools._task_execution import context_log, persistence_requires_task
from ark_mcp.tools._vod_fetchers import _VOD_FETCHERS, VodTaskSnapshot, persist_vod_outputs
from ark_mcp.tools._vod_shared import VodOperation


class VodGetTaskInput(BaseModel):
    """Input for ``vod_get_task``."""

    task_id: str = Field(
        ...,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="MediaKit task ID returned by a vod_* create tool.",
    )
    operation: VodOperation = Field(
        ...,
        description=(
            "Operation that created the task; echoed as `operation` by every vod_* create tool "
            "(enhance, transcode, separate_audio, add_subtitles, remove_subtitles)."
        ),
    )
    persist_output: bool = Field(
        False,
        description=(
            "Persist finished outputs as durable artifacts, once per task. Requires background "
            "execution (ark_job_submit or native task augmentation); leave false for a "
            "foreground status poll."
        ),
    )
    output_path: str | None = output_path_field(
        "the output (a directory ending in '/' is required for separate_audio, which writes "
        "every track); requires persist_output=true"
    )
    overwrite: bool = overwrite_field()


class VodTaskOutput(BaseModel):
    """Normalized status and outputs of a VOD AI MediaKit task."""

    provider: Literal["byteplus-vod-mediakit"] = Field(
        "byteplus-vod-mediakit", description="Provider surface that processed the task."
    )
    task_id: str = Field(..., description="MediaKit task ID.")
    operation: VodOperation = Field(..., description="Operation that created the task.")
    status: VodTaskStatus = Field(
        ...,
        description=(
            "Normalized task status: processing (queued or running; poll again), succeeded, or "
            "failed."
        ),
    )
    provider_status: str | None = Field(
        None, description="Raw provider status label, when returned."
    )
    request_id: str | None = Field(
        None, description="Provider diagnostic request ID, when returned."
    )
    created_at: str | None = Field(None, description="ISO-8601 task creation time.")
    finished_at: str | None = Field(None, description="ISO-8601 terminal time.")
    duration_seconds: float | None = Field(
        None,
        ge=0,
        description="Output (or, for separate_audio, input) duration in seconds, when reported.",
    )
    resolution: str | None = Field(
        None,
        description="Output resolution label, when reported (enhance, transcode, subtitles).",
    )
    fps: float | None = Field(
        None, gt=0, description="Output frame rate in frames per second, when reported (enhance)."
    )
    video_codec: str | None = Field(
        None, description="Output video codec, when reported (transcode)."
    )
    tool_version: str | None = Field(None, description="Enhancement tier, when reported (enhance).")
    outputs: list[TaskOutputFile] = Field(
        ...,
        description=(
            "Finished outputs (role video, or voice/background/music/sfx for separate_audio); "
            "empty until succeeded."
        ),
    )
    error: TaskError | None = Field(
        None, description="Provider failure code and safe message when status is failed."
    )

    @model_validator(mode="after")
    def _validate_state(self) -> VodTaskOutput:
        if self.status == "processing":
            if self.outputs or self.error is not None:
                raise ValueError("processing output must not carry outputs or an error")
        elif self.status == "succeeded":
            if not self.outputs:
                raise ValueError("succeeded output requires at least one output")
            if self.error is not None:
                raise ValueError("succeeded output must not carry a provider failure")
            for output in self.outputs:
                _validate_output_file(output)
        else:
            if self.error is None:
                raise ValueError("failed output requires provider failure detail")
            if self.outputs:
                raise ValueError("failed output must not carry outputs")
        return self


def _validate_output_file(output: TaskOutputFile) -> None:
    if output.source_url is None:
        raise ValueError("succeeded outputs require source_url")
    if output.persistence == "persisted" and (
        output.artifact is None or output.persistence_issue is not None
    ):
        raise ValueError("persisted output requires an artifact and forbids a persistence issue")
    if output.persistence == "failed" and output.persistence_issue is None:
        raise ValueError("failed persistence requires a persistence issue")
    if output.persistence == "not_requested" and (
        output.artifact is not None or output.persistence_issue is not None
    ):
        raise ValueError("not_requested persistence forbids an artifact and persistence issue")


async def _prepare_export(
    input: VodGetTaskInput, ctx: Context, runtime: RuntimeServices
) -> LocalExportTarget | None:
    if input.output_path is None:
        return None
    if message := needs_persist_output(input):
        raise ValueError(message)
    return await prepare_local_export(
        output_path=input.output_path,
        overwrite=input.overwrite,
        ctx=ctx,
        settings=runtime.settings,
        require_dir=input.operation == "separate_audio",
    )


async def _export_outputs(
    outputs: list[TaskOutputFile],
    target: LocalExportTarget | None,
    runtime: RuntimeServices,
    owner: PrincipalContext,
) -> list[TaskOutputFile]:
    if target is None:
        return outputs
    return [
        output.model_copy(
            update={
                "artifact": await export_ref(
                    runtime.artifact_store, output.artifact, target, auth=owner
                )
            }
        )
        for output in outputs
    ]


def _build_output(
    snapshot: VodTaskSnapshot, operation: VodOperation, outputs: list[TaskOutputFile]
) -> VodTaskOutput:
    return VodTaskOutput(
        task_id=snapshot.task_id,
        operation=operation,
        status=snapshot.status,
        provider_status=snapshot.provider_status,
        request_id=snapshot.request_id,
        created_at=snapshot.created_at,
        finished_at=snapshot.finished_at,
        duration_seconds=snapshot.duration_seconds,
        resolution=snapshot.resolution,
        fps=snapshot.fps,
        video_codec=snapshot.video_codec,
        tool_version=snapshot.tool_version,
        outputs=outputs,
        error=snapshot.error,
    )


async def vod_get_task(input: VodGetTaskInput, ctx: Context) -> VodTaskOutput | ToolResult:
    """Poll the status and outputs of any BytePlus VOD AI MediaKit task.

    Call it with the task_id and the operation returned by the vod_* create
    tool (vod_enhance_video, vod_transcode_video, vod_separate_audio,
    vod_add_subtitles, or vod_remove_subtitles). A queued or processing task
    is not a failure: poll again later and never resubmit the create tool.
    With persist_output=true (the default, which needs background execution
    through ark_job_submit or native task augmentation), the first successful
    poll copies each expiring output into durable artifact storage once per
    task; use persist_output=false for a foreground status check. Outputs keep
    their expiring source_url even when persistence is skipped or fails.
    """
    runtime = get_runtime(ctx)
    target = await _prepare_export(input, ctx, runtime)
    task_error = persistence_requires_task(ctx, input.persist_output)
    if task_error is not None:
        return task_error
    fetcher = _VOD_FETCHERS[input.operation]
    await context_log(
        ctx, "info", f"Retrieving VOD AI MediaKit {fetcher.label} task {input.task_id}"
    )
    await ctx.report_progress(progress=20, total=100)
    owner = get_principal(ctx)
    await runtime.ownership_store.require_owner("vod-mediakit", input.task_id, owner)

    try:
        snapshot = await fetcher.fetch(input.task_id)
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to retrieve {fetcher.label} task: {exc.message}")
        return provider_error_result(exc)

    await ctx.report_progress(progress=60, total=100)
    outputs: list[TaskOutputFile] = []
    if snapshot.status == "succeeded":
        outputs = await persist_vod_outputs(
            ctx, owner, snapshot, persist_output=input.persist_output, label=fetcher.label
        )
        outputs = await _export_outputs(outputs, target, runtime, owner)
    log_info(
        "vod_get_task_complete",
        task_id=snapshot.task_id,
        operation=input.operation,
        status=snapshot.status,
        persistence=[output.persistence for output in outputs],
        failure_code=snapshot.error.code if snapshot.error else None,
    )
    return _build_output(snapshot, input.operation, outputs)


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
