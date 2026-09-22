"""Shared domain models used across tool input/output contracts.

These types are used in multiple tool output models. Tool-specific input
models live alongside their tool handlers in ``tools/``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ark_mcp.domain.artifacts import ArtifactRef


class SeedanceTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> SeedanceTaskStatus:
        return cls.UNKNOWN


class Seed3DTaskStatus(StrEnum):
    """Task lifecycle states for 3D generation (identical to Seedance)."""

    QUEUED = "queued"
    RUNNING = "running"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> Seed3DTaskStatus:
        return cls.UNKNOWN


class SubtitleUtterance(BaseModel):
    """Utterance-level subtitle timing returned by Seed Audio."""

    model_config = ConfigDict(extra="allow")

    text: str = Field("", description="Utterance text.")
    start: float | None = Field(None, description="Start time in seconds.")
    end: float | None = Field(None, description="End time in seconds.")


class SubtitleWord(BaseModel):
    """Word-level subtitle timing returned by Seed Audio."""

    model_config = ConfigDict(extra="allow")

    text: str = Field("", description="Word text.")
    start: float | None = Field(None, description="Start time in seconds.")
    end: float | None = Field(None, description="End time in seconds.")


class Subtitle(BaseModel):
    """Timestamped subtitle data for Seed Audio output."""

    utterances: list[SubtitleUtterance] = Field(
        default_factory=list,
        description="Utterance-level subtitle entries with timestamps.",
    )
    words: list[SubtitleWord] = Field(
        default_factory=list,
        description="Word-level subtitle entries with timestamps.",
    )


class SeedreamItemError(BaseModel):
    """Error for a single failed image in a batch generation."""

    index: int = Field(..., description="0-based index of the failed image.")
    code: str | None = Field(default=None, description="Provider error code.")
    message: str = Field(..., description="Error description.")


class SeedreamUsage(BaseModel):
    """Usage information for a Seedream image generation call."""

    prompt_tokens: int | None = Field(None, description="Prompt tokens consumed.")
    completion_tokens: int | None = Field(None, description="Completion tokens consumed.")
    total_tokens: int | None = Field(None, description="Total tokens consumed.")


class SeedanceTaskUsage(BaseModel):
    """Usage/billing information for a completed Seedance task."""

    completion_tokens: int | None = Field(default=None, description="Total tokens consumed.")
    prompt_tokens: int | None = Field(default=None, description="Prompt tokens consumed.")


class SeedanceTaskSummary(BaseModel):
    """Summary of a Seedance task for list results."""

    task_id: str = Field(..., description="Provider task ID.")
    model: str = Field(..., description="Model ID used for generation.")
    status: SeedanceTaskStatus = Field(..., description="Current task status.")
    created_at: str = Field(..., description="ISO-8601 timestamp of task creation.")
    updated_at: str = Field(..., description="ISO-8601 timestamp of last status update.")


class VariationError(BaseModel):
    """Machine-readable failure for one variation."""

    code: str = Field(
        ..., description="Error code (e.g. PROVIDER_ERROR, TIMEOUT, UNEXPECTED_ERROR)."
    )
    message: str = Field("", description="Human-readable error message.")
    request_id: str | None = Field(None, description="Provider request ID, if available.")
    retryable: bool = Field(False, description="Whether the caller may retry this variation.")
    ambiguous_completion: bool = Field(
        False, description="Whether the provider may have partially completed despite the error."
    )
    phase: Literal["queued", "generating", "persisting"] | None = Field(
        None,
        description=(
            "Stage the variation was in when it failed: 'queued' (never started, safe to "
            "retry), 'generating' (provider call in flight, may have completed), or "
            "'persisting' (output generated, storing it failed). None when not applicable."
        ),
    )


class SeedanceTaskError(BaseModel):
    """Typed task failure returned by Seedance."""

    code: str = Field("", description="Provider error code.")
    message: str = Field("", description="Error description.")


class SeedanceTaskSettings(BaseModel):
    """Known Seedance settings, preserving provider extensions."""

    model_config = ConfigDict(extra="allow")

    resolution: str | None = Field(None, description="Output video resolution.")
    ratio: str | None = Field(None, description="Output aspect ratio (e.g. 16:9).")
    duration: int | str | None = Field(None, description="Video duration in seconds, or 'auto'.")
    omni_reference_task_type: str | None = Field(
        None,
        description="Provider task type hint (e.g. auto, edit_video, extend_video).",
    )
    generate_audio: bool | None = Field(None, description="Whether an audio track was generated.")
    return_last_frame: bool | None = Field(
        None, description="Whether the last frame was returned as a separate image."
    )
    service_tier: str | None = Field(None, description="Service tier used (default or flex).")
    priority: int | None = Field(None, description="Task priority (0-9).")


class Seed3DTaskUsage(BaseModel):
    """Usage/billing information for a completed Seed3D task."""

    completion_tokens: int | None = Field(
        default=None, description="Tokens consumed generating 3D."
    )
    total_tokens: int | None = Field(
        default=None, description="Total tokens consumed (equals completion_tokens for 3D)."
    )


class Seed3DTaskSummary(BaseModel):
    """Summary of a Seed3D task for list results."""

    task_id: str = Field(..., description="Provider task ID.")
    model: str = Field(..., description="Model ID used for generation.")
    status: Seed3DTaskStatus = Field(..., description="Current task status.")
    created_at: str = Field(..., description="ISO-8601 timestamp of task creation.")
    updated_at: str = Field(..., description="ISO-8601 timestamp of last status update.")


class Seed3DTaskError(BaseModel):
    """Typed task failure returned by Seed3D."""

    code: str = Field("", description="Provider error code.")
    message: str = Field("", description="Error description.")


class VariationResult(BaseModel):
    """Result of a single variation within a parallel generation."""

    index: int = Field(..., description="0-based variation index.")
    seed: int | None = Field(None, description="Seed used (if applicable).")
    artifact: ArtifactRef | None = Field(None, description="Generated artifact (None if failed).")
    task_id: str | None = Field(
        None,
        description=(
            "Provider task ID for Seedance polling, obtained from the enclosing "
            "MCP task's terminal tasks/get result."
        ),
    )
    error: VariationError | None = Field(None, description="Error if this variation failed.")
    request_id: str | None = Field(None, description="Client request ID for this variation.")
    provider_log_id: str | None = Field(
        None, description="Provider-side log ID for troubleshooting."
    )


class VariationSummary(BaseModel):
    """Aggregate result of a parallel generation."""

    total: int = Field(..., description="Total variations requested.")
    succeeded: int = Field(..., description="Variations that produced output.")
    failed: int = Field(..., description="Variations that failed.")
    variations: list[VariationResult] = Field(
        default_factory=list, description="Per-variation results (artifacts, errors, and metadata)."
    )


class UnderstandingUsage(BaseModel):
    """Token usage for a Seed 2.1 multimodal understanding call."""

    prompt_tokens: int = Field(..., description="Number of input (prompt) tokens consumed.")
    completion_tokens: int = Field(
        ..., description="Number of output (completion) tokens consumed, including reasoning."
    )
    total_tokens: int = Field(..., description="Total tokens consumed (prompt + completion).")
    reasoning_tokens: int | None = Field(
        None,
        description=(
            "Completion tokens spent on internal deep thinking, when the provider reports it. "
            "The reasoning text itself is never returned."
        ),
    )


class SchemaViolation(BaseModel):
    """Why a JSON answer did not parse or did not satisfy the requested JSON Schema."""

    path: str = Field(
        ...,
        description=(
            "JSON Pointer-style location of the first violation (e.g. '/beats/2/start'), "
            "or '' when the answer is not valid JSON at all."
        ),
    )
    message: str = Field(..., description="Human-readable validation or parse error.")
    finish_reason: str = Field(
        ...,
        description=(
            "Finish reason of the completion. 'length' means the answer was cut off by "
            "max_tokens; raise max_tokens rather than retrying."
        ),
    )


class UnderstandingChoice(BaseModel):
    """A single completion choice returned by the Seed 2.1 model.

    Only the final answer is returned. The model always thinks, but its
    reasoning trace is discarded and never included in tool output.
    """

    role: Literal["assistant"] = Field(
        "assistant", description="Message role (always 'assistant')."
    )
    content: str = Field(
        ...,
        description=(
            "The model's final answer. Truncated to the first 2,000 characters when "
            "return_content='summary', and empty when return_content='none'."
        ),
    )
    content_chars: int = Field(
        ..., description="Length of the full answer in characters, before any truncation."
    )
    content_truncated: bool = Field(
        False, description="True when content is shorter than the full answer."
    )
    parsed: dict[str, Any] | list[Any] | None = Field(
        None,
        description=(
            "The answer parsed as JSON, set when a json_object or json_schema response_format "
            "was requested and the answer parsed (and, for json_schema, validated). "
            "Returned even when return_content is 'summary' or 'none'."
        ),
    )
    schema_violation: SchemaViolation | None = Field(
        None,
        description=(
            "Set when a JSON response_format was requested but the answer did not parse or "
            "did not satisfy the schema. The raw answer is still returned in content."
        ),
    )
    finish_reason: str = Field(
        ...,
        description="Why generation stopped: 'stop', 'length', 'tool_calls', or 'content_filter'.",
    )
