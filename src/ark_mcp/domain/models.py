"""Shared domain models used across tool input/output contracts.

These types are used in multiple tool output models. Tool-specific input
models live alongside their tool handlers in ``tools/``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ark_mcp.domain.artifacts import ArtifactPersistenceIssue, ArtifactRef
from ark_mcp.domain.media import HttpsUrl


class GenTaskStatus(StrEnum):
    """Lifecycle state of a ModelArk generation task (Seedance, Hyper3D, Hitem3d)."""

    QUEUED = "queued"
    RUNNING = "running"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> GenTaskStatus:
        return cls.UNKNOWN


VodTaskStatus = Literal["processing", "succeeded", "failed"]

TaskOutputRole = Literal["video", "last_frame", "model_file", "voice", "background", "music", "sfx"]


class TaskError(BaseModel):
    """Safe provider failure detail for an asynchronous task."""

    code: str | None = Field(None, description="Provider failure code, when returned.")
    message: str = Field("", description="Failure explanation without credentials or signed URLs.")


class TaskUsage(BaseModel):
    """Token usage reported for a finished ModelArk generation task."""

    prompt_tokens: int | None = Field(None, description="Prompt tokens consumed, when reported.")
    completion_tokens: int | None = Field(
        None, description="Completion tokens consumed, when reported."
    )
    total_tokens: int | None = Field(None, description="Total tokens consumed, when reported.")


class GenTaskLookupError(BaseModel):
    """A task ID that could not be checked."""

    task_id: str = Field(..., description="The task ID that could not be checked.")
    code: str = Field(
        ...,
        description="NOT_OWNED (not created by this caller), NOT_FOUND, or a provider error code.",
    )
    message: str = Field(..., description="Human-readable reason.")


class TaskOutputFile(BaseModel):
    """One output file of a finished task (ModelArk generation or VOD MediaKit)."""

    role: TaskOutputRole = Field(
        ...,
        description=(
            "Output kind: video, last_frame (Seedance), model_file (3D), or voice, background, "
            "music, sfx (VOD audio separation)."
        ),
    )
    artifact: ArtifactRef | None = Field(
        None,
        description="Durable artifact when persisted.",
    )
    source_url: HttpsUrl | None = Field(
        None,
        description="Expiring provider URL (kept when persistence is skipped or fails).",
    )
    source_expires_at: str | None = Field(None, description="ISO-8601 expiry of source_url.")
    persistence: Literal["not_requested", "persisted", "failed"] = Field(
        ..., description="Persistence outcome, independent of task success."
    )
    persistence_issue: ArtifactPersistenceIssue | None = Field(
        None, description="Safe explanation when persistence failed."
    )


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
    input_images: int | None = Field(None, description="Input image count reported by provider.")
    generated_images: int | None = Field(None, description="Generated image count.")
    output_tokens: int | None = Field(None, description="Output tokens reported by provider.")


class SeedreamImageInspection(BaseModel):
    """Local byte inspection, independent of provider reports and visual quality."""

    status: Literal["verified", "mismatch", "invalid", "unavailable"] = Field(
        ...,
        description="Byte checks passed, claims differ, bytes invalid, or inspection unavailable.",
    )
    width: int | None = Field(default=None, description="Decoded width in pixels.")
    height: int | None = Field(default=None, description="Decoded height in pixels.")
    output_format: str | None = Field(default=None, description="Byte-detected png or jpeg format.")
    has_transparency: bool | None = Field(
        default=None,
        description="True when decoded pixels contain alpha below 255; null if unchecked.",
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Safe byte-check or claim-mismatch codes; no paid retries.",
    )


class SeedreamImageMetadata(BaseModel):
    """Provider-reported image properties, not inferred from the request."""

    index: int = Field(..., description="Zero-based position in provider data.")
    artifact_id: str | None = Field(
        None, description="Matching artifact ID, or null without output."
    )
    size: str | None = Field(None, description="Returned WIDTHxHEIGHT in pixels; null if absent.")
    output_format: str | None = Field(None, description="Returned image format; null if absent.")
    background: Literal["opaque", "transparent"] | None = Field(
        None, description="Actual provider-reported background; null if absent."
    )
    inspection: SeedreamImageInspection | None = Field(
        default=None, description="Local byte checks; null for URL-only delivery or no artifact."
    )


class SeedreamGenerationMetadata(BaseModel):
    """Metadata for one completed Seedream provider call."""

    variation_index: int = Field(..., description="Zero-based variation index; 0 for single calls.")
    request_id: str | None = Field(default=None, description="Provider request ID; null if absent.")
    seed: int | None = Field(
        default=None,
        description="Submitted seed; -1 or null means provider randomness, not a recovered seed.",
    )
    prompt_sha256: str | None = Field(
        default=None, description="SHA-256 of the UTF-8 submitted prompt including edit markup."
    )
    request_options: dict[str, str | int | bool] = Field(
        default_factory=dict,
        description="Allowlisted submitted options and reference count; excludes prompt, media and identifiers. Omitted options use provider defaults.",
    )
    images: list[SeedreamImageMetadata] = Field(
        default_factory=list, description="Image metadata in provider data order."
    )
    safety_identifier: str | None = Field(
        None, description="Provider-echoed end-user identifier; null if absent."
    )
    usage: SeedreamUsage | None = Field(None, description="Per-call usage; null if absent.")


class SeedanceQueueInfo(BaseModel):
    """Server-derived queue timing for a Seedance task.

    ModelArk publishes no queue position or ETA. These values are computed by
    this server from the task's timestamps and expiry setting; they are
    estimates, not a provider SLA.
    """

    queued_seconds: int | None = Field(
        None,
        description=(
            "Seconds spent queued: now minus created_at while queued, or updated_at minus "
            "created_at once running (approximate). None for finished tasks."
        ),
    )
    running_seconds: int | None = Field(
        None,
        description="Seconds since the last status update while running (approximate).",
    )
    service_tier: str | None = Field(
        None, description="Service tier ('default' or 'flex'). Flex tasks usually queue longer."
    )
    expires_at: str | None = Field(
        None,
        description=(
            "ISO-8601 time after which the provider fails a task that has not finished: "
            "created_at + execution_expires_after. None for finished tasks."
        ),
    )
    expires_at_estimated: bool = Field(
        False,
        description=(
            "True when the provider did not report execution_expires_after and the documented "
            "default (48 hours) was assumed."
        ),
    )
    hint: str | None = Field(
        None,
        description="Human-readable queue summary for reporting to the user.",
    )


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
            "Stage at failure: 'queued' (never started, safe to retry), 'generating' (may have "
            "completed), or 'persisting' (generated, storing failed)."
        ),
    )


class SeedanceTaskSettings(BaseModel):
    """Known Seedance settings, preserving provider extensions."""

    model_config = ConfigDict(extra="allow")

    resolution: str | None = Field(None, description="Output video resolution.")
    ratio: str | None = Field(None, description="Output aspect ratio (e.g. 16:9).")
    duration: int | str | None = Field(None, description="Video duration in seconds, or 'auto'.")
    omni_reference_task_type: str | None = Field(
        None,
        description="Provider task type hint echoed by Seedance 2.5: auto, reference, edit, or extend.",
    )
    generate_audio: bool | None = Field(None, description="Whether an audio track was generated.")
    return_last_frame: bool | None = Field(
        None, description="Whether the last frame was returned as a separate image."
    )
    service_tier: str | None = Field(None, description="Service tier used (default or flex).")
    priority: int | None = Field(None, description="Task priority (0-9).")


class VariationResult(BaseModel):
    """Result of a single variation within a parallel generation."""

    index: int = Field(..., description="0-based variation index.")
    seed: int | None = Field(None, description="Seed used (if applicable).")
    artifact: ArtifactRef | None = Field(None, description="Generated artifact (None if failed).")
    task_id: str | None = Field(
        None,
        description="Provider task ID, when the variation created a task.",
    )
    error: VariationError | None = Field(None, description="Error if this variation failed.")
    request_id: str | None = Field(None, description="Request ID for this variation.")
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
