"""Compatibility adapter over the FastMCP task execution backend."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from inspect import signature
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, cast

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import without_injected_parameters
from fastmcp.tools import FunctionTool
from fastmcp.utilities.tasks import DEFAULT_POLL_INTERVAL_MS
from fastmcp_tasks.creation import create_task
from fastmcp_tasks.handlers import tasks_cancel, tasks_get

from ark_mcp.domain.background_jobs import (
    BackgroundJobAccepted,
    BackgroundJobError,
    BackgroundJobSnapshot,
    BackgroundJobToolResult,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastmcp import Context, FastMCP
    from fastmcp.tools.base import Tool
    from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class BackgroundToolSpec:
    """Task mode and authorization scope for one compatibility target."""

    mode: Literal["required", "optional"]
    required_scope: str


BACKGROUND_TOOL_SPECS: Mapping[str, BackgroundToolSpec] = MappingProxyType(
    {
        "media_upload": BackgroundToolSpec("required", "media:upload"),
        "seed_audio_generate": BackgroundToolSpec("required", "seed:audio:generate"),
        "seed_audio_generate_variations": BackgroundToolSpec("required", "seed:audio:generate"),
        "speech_to_text": BackgroundToolSpec("required", "seed:asr:transcribe"),
        "seed_understand": BackgroundToolSpec("required", "understanding:read"),
        "seedream_generate_image": BackgroundToolSpec("required", "seedream:generate"),
        "seedream_edit_image": BackgroundToolSpec("required", "seedream:generate"),
        "seedream_generate_image_variations": BackgroundToolSpec("required", "seedream:generate"),
        "seedance_create_task": BackgroundToolSpec("required", "seedance:create"),
        "seedance_create_task_variations": BackgroundToolSpec("required", "seedance:create"),
        "seedance_2_5_create_task": BackgroundToolSpec("required", "seedance:create"),
        "seedance_2_5_create_task_variations": BackgroundToolSpec("required", "seedance:create"),
        "hyper3d_create_task": BackgroundToolSpec("required", "hyper3d:create"),
        "hitem3d_create_task": BackgroundToolSpec("required", "hitem3d:create"),
        "vod_enhance_video": BackgroundToolSpec("required", "vod:enhance"),
        "vod_transcode_video": BackgroundToolSpec("required", "vod:transcode"),
        "vod_separate_audio": BackgroundToolSpec("required", "vod:extract"),
        "vod_add_subtitles": BackgroundToolSpec("required", "vod:subtitle:add"),
        "vod_remove_subtitles": BackgroundToolSpec("required", "vod:subtitle:remove"),
        "seedance_get_task": BackgroundToolSpec("optional", "seedance:read"),
        "hyper3d_get_task": BackgroundToolSpec("optional", "hyper3d:read"),
        "hitem3d_get_task": BackgroundToolSpec("optional", "hitem3d:read"),
        "vod_get_enhancement_task": BackgroundToolSpec("optional", "vod:read"),
        "vod_get_transcode_task": BackgroundToolSpec("optional", "vod:read"),
        "vod_get_audio_separation": BackgroundToolSpec("optional", "vod:read"),
        "vod_get_subtitle_addition_task": BackgroundToolSpec("optional", "vod:read"),
        "vod_get_subtitle_removal_task": BackgroundToolSpec("optional", "vod:read"),
    }
)


def background_tool_spec(tool_name: str) -> BackgroundToolSpec | None:
    """Return the compatibility policy for a task-enabled tool."""
    return BACKGROUND_TOOL_SPECS.get(tool_name)


def required_background_tool_names() -> frozenset[str]:
    """Return tools that reject foreground execution."""
    return frozenset(
        name for name, spec in BACKGROUND_TOOL_SPECS.items() if spec.mode == "required"
    )


def optional_background_tool_names() -> frozenset[str]:
    """Return tools that optionally use background execution."""
    return frozenset(
        name for name, spec in BACKGROUND_TOOL_SPECS.items() if spec.mode == "optional"
    )


class BackgroundJobBridge:
    """Translate stable Ark job operations to the pinned FastMCP task backend."""

    async def submit(
        self,
        context: Context,
        target: Tool,
        arguments: dict[str, JsonValue],
    ) -> BackgroundJobAccepted:
        """Validate and durably enqueue one registered task-enabled tool."""
        if isinstance(target, FunctionTool):
            target_signature = signature(
                without_injected_parameters(target.fn, run_in_thread=target.run_in_thread)
            )
            try:
                target_signature.bind(**arguments)
            except TypeError as exc:
                raise ToolError(
                    f"Invalid arguments for background target {target.name!r}."
                ) from exc
        created = await create_task(
            target,
            {key: cast("object", value) for key, value in arguments.items()},
            context,
        )
        return BackgroundJobAccepted(
            job_id=created.task_id,
            target_tool=target.name,
            status="working",
            created_at=datetime.fromisoformat(created.created_at.replace("Z", "+00:00")),
            ttl_ms=created.ttl_ms,
            poll_after_ms=created.poll_interval_ms or DEFAULT_POLL_INTERVAL_MS,
        )

    async def get(self, server: FastMCP, job_id: str) -> BackgroundJobSnapshot:
        """Return one job state through the existing FastMCP result handler."""
        task = await tasks_get(server, job_id)
        result = self._tool_result(task.result)
        error = self._task_error(task.error)
        if task.status == "input_required":
            error = BackgroundJobError(
                code=-32602,
                message=(
                    "This job requires additional input, which the ark_job_* API does not "
                    "support. Cancel the job, or run the tool with MCP task augmentation on a "
                    "client that supports it."
                ),
                data=None,
            )
        return BackgroundJobSnapshot(
            job_id=task.task_id,
            status=task.status,
            created_at=datetime.fromisoformat(task.created_at.replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(task.last_updated_at.replace("Z", "+00:00")),
            ttl_ms=task.ttl_ms,
            poll_after_ms=task.poll_interval_ms or DEFAULT_POLL_INTERVAL_MS,
            result=result,
            error=error,
        )

    async def cancel(self, server: FastMCP, job_id: str) -> None:
        """Cancel one job through the existing FastMCP cancellation handler."""
        await tasks_cancel(server, job_id)

    @staticmethod
    def _tool_result(raw: dict[str, object] | None) -> BackgroundJobToolResult | None:
        if raw is None:
            return None
        structured_content = raw.get("structuredContent")
        meta = raw.get("_meta")
        return BackgroundJobToolResult(
            content=cast("list[JsonValue]", raw.get("content") or []),
            structured_content=(
                cast("dict[str, JsonValue]", structured_content)
                if isinstance(structured_content, dict)
                else None
            ),
            is_error=bool(raw.get("isError", False)),
            meta=cast("dict[str, JsonValue]", meta) if isinstance(meta, dict) else None,
        )

    @staticmethod
    def _task_error(raw: dict[str, object] | None) -> BackgroundJobError | None:
        if raw is None:
            return None
        raw_code = raw.get("code")
        raw_message = raw.get("message")
        raw_data = raw.get("data")
        return BackgroundJobError(
            code=raw_code if isinstance(raw_code, int) else -32603,
            message=raw_message if isinstance(raw_message, str) else "Background job failed.",
            data=cast("JsonValue", raw_data) if raw_data is not None else None,
        )


background_job_bridge = BackgroundJobBridge()
