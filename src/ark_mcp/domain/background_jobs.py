"""Stable public models for ordinary-tool background job compatibility."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, JsonValue

BackgroundJobStatus = Literal[
    "working",
    "input_required",
    "completed",
    "failed",
    "cancelled",
]
BackgroundTaskMode = Literal["required", "optional"]


class BackgroundJobError(BaseModel):
    """Error details for a failed or unsupported background job state."""

    code: int = Field(..., description="JSON-RPC-compatible error code.")
    message: str = Field(..., description="Human-readable error message.")
    data: JsonValue | None = Field(
        None,
        description="Optional structured error details supplied by the task backend.",
    )


class BackgroundJobToolResult(BaseModel):
    """Original MCP tool result returned by a completed background job."""

    content: list[JsonValue] = Field(
        default_factory=list,
        description="MCP content blocks returned by the target tool.",
    )
    structured_content: dict[str, JsonValue] | None = Field(
        None,
        description="Structured output returned by the target tool, when available.",
    )
    is_error: bool = Field(
        False,
        description="Whether the completed target tool returned an MCP tool error result.",
    )
    meta: dict[str, JsonValue] | None = Field(
        None,
        description="Optional MCP result metadata returned by the target tool.",
    )


class BackgroundJobAccepted(BaseModel):
    """Handle returned immediately after a background job is durably accepted."""

    job_id: str = Field(
        ...,
        min_length=20,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description=(
            "Server-generated Ark background job ID. This is distinct from any provider task "
            "ID returned inside the completed target result."
        ),
    )
    target_tool: str = Field(
        ...,
        description="Task-enabled Ark MCP tool executing in the background.",
    )
    status: Literal["working"] = Field(
        "working",
        description="Initial job status after durable acceptance.",
    )
    created_at: AwareDatetime = Field(
        ...,
        description="ISO-8601 timestamp when the job was created.",
    )
    ttl_ms: int | None = Field(
        ...,
        ge=0,
        description="Result retention duration in milliseconds, or null when unlimited.",
    )
    poll_after_ms: int = Field(
        ...,
        ge=0,
        description="Recommended delay in milliseconds before polling this job.",
    )


class BackgroundJobSnapshot(BaseModel):
    """Current state and optional terminal result for a background job."""

    job_id: str = Field(
        ...,
        min_length=20,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Server-generated Ark background job ID.",
    )
    status: BackgroundJobStatus = Field(
        ...,
        description="Current job state.",
    )
    created_at: AwareDatetime = Field(
        ...,
        description="ISO-8601 timestamp when the job was created.",
    )
    updated_at: AwareDatetime = Field(
        ...,
        description="ISO-8601 timestamp of the latest observed job state.",
    )
    ttl_ms: int | None = Field(
        ...,
        ge=0,
        description="Configured result retention duration in milliseconds, or null when unlimited.",
    )
    poll_after_ms: int = Field(
        ...,
        ge=0,
        description="Recommended delay in milliseconds before the next poll.",
    )
    result: BackgroundJobToolResult | None = Field(
        None,
        description="Original target-tool result when the job completed.",
    )
    error: BackgroundJobError | None = Field(
        None,
        description="Task execution error or unsupported input-required state, when present.",
    )


class BackgroundJobCancelled(BaseModel):
    """Acknowledgement returned after a background job is cancelled."""

    job_id: str = Field(
        ...,
        min_length=20,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Server-generated Ark background job ID that was cancelled.",
    )
    status: Literal["cancelled"] = Field(
        "cancelled",
        description="Terminal cancellation state.",
    )


class BackgroundJobTarget(BaseModel):
    """One configured tool that can be submitted as an Ark background job."""

    tool_name: str = Field(..., description="Registered Ark MCP target tool name.")
    task_mode: BackgroundTaskMode = Field(
        ...,
        description="Native MCP task execution mode for this target.",
    )
    required_scope: str = Field(
        ...,
        description="Tool permission required to submit this target in shared-key HTTP mode.",
    )
    description: str = Field(
        ...,
        description="Description of the original target tool.",
    )
    input_schema: dict[str, JsonValue] = Field(
        ...,
        description="JSON Schema for the original target tool arguments.",
    )


class BackgroundJobCapabilities(BaseModel):
    """Configured and authorized background job targets visible to the caller."""

    targets: list[BackgroundJobTarget] = Field(
        default_factory=list,
        description="Task-enabled targets available to the current caller.",
    )
