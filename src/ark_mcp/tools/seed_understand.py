"""``seed_understand`` tool — multimodal understanding through Seed 2.1 via ModelArk.

The handler validates media inputs, builds an OpenAI-compatible Chat Completions
request with image/video content parts, and returns only the model's final
answer. Deep thinking is always enabled (depth set by ``reasoning_effort``);
the reasoning trace is discarded and never returned, logged, or saved.
Optional ``response_format`` enforces JSON or a JSON Schema at generation
time, and ``save_to`` writes the answer straight to a local file. The server
registers the handler as a required MCP background task and forces
``stream: false`` for the provider.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import get_capability_registry
from ark_mcp.domain.artifacts import MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import MediaSource
from ark_mcp.domain.models import SchemaViolation, UnderstandingChoice, UnderstandingUsage
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.modelark.schemas import ChatCompletionProviderResponse
from ark_mcp.providers.modelark.understanding import SeedUnderstandingService
from ark_mcp.providers.retry import RetryPolicy, call_with_retry
from ark_mcp.runtime import billed_provider_slot
from ark_mcp.security.output_paths import (
    OutputPathError,
    validate_output_target,
    write_output_file,
)
from ark_mcp.tools._cost import log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log

# Chat timeouts are safe to retry but are not auto-retried: a second full-length
# thinking run would double latency and token cost. 429/5xx are still retried.
_UNDERSTANDING_RETRY_POLICY = RetryPolicy(retry_timeouts=False)

_SUMMARY_CHARS = 2000
_MAX_SCHEMA_BYTES = 64 * 1024


class UnderstandingImageInput(MediaSource):
    """Image input for multimodal understanding."""

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.IMAGE


class UnderstandingVideoInput(MediaSource):
    """Video input for multimodal understanding. URL only — Base64 is not supported."""

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.VIDEO

    @model_validator(mode="before")
    @classmethod
    def reject_video_base64(cls, data: object) -> object:
        if isinstance(data, dict) and data.get("kind") == "base64":
            raise ValueError(
                "Video Base64 is not supported by the chat endpoint; "
                "upload via media_upload and pass a URL."
            )
        return data


class UnderstandingJsonSchema(BaseModel):
    """JSON Schema the answer must satisfy, enforced by the provider during decoding."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="Schema name sent to the provider (1-64 chars: letters, digits, '_', '-').",
    )
    schema_: dict[str, Any] = Field(
        ...,
        alias="schema",
        description=(
            "JSON Schema (draft 2020-12) the answer must satisfy, for example a "
            "template-factory schema passed verbatim. At most 64 KB when serialized."
        ),
    )
    description: str | None = Field(
        None, max_length=2000, description="Optional description of the schema's purpose."
    )
    strict: bool = Field(
        True, description="Ask the provider to enforce the schema strictly during decoding."
    )


class UnderstandingResponseFormat(BaseModel):
    """Output format constraint for the answer."""

    type: Literal["text", "json_object", "json_schema"] = Field(
        ...,
        description=(
            "'text' (default behavior), 'json_object' (any valid JSON object), or "
            "'json_schema' (JSON that satisfies json_schema, enforced by the provider)."
        ),
    )
    json_schema: UnderstandingJsonSchema | None = Field(
        None, description="Required when type='json_schema'; must be omitted otherwise."
    )

    @model_validator(mode="after")
    def _check_schema(self) -> UnderstandingResponseFormat:
        if self.type == "json_schema":
            if self.json_schema is None:
                raise ValueError("response_format.json_schema is required when type='json_schema'.")
            if len(json.dumps(self.json_schema.schema_)) > _MAX_SCHEMA_BYTES:
                raise ValueError("response_format.json_schema.schema must be at most 64 KB.")
            try:
                Draft202012Validator.check_schema(self.json_schema.schema_)
            except SchemaError as exc:
                raise ValueError(
                    f"response_format.json_schema.schema is invalid: {exc.message}"
                ) from exc
        elif self.json_schema is not None:
            raise ValueError("response_format.json_schema is only allowed when type='json_schema'.")
        return self

    def provider_payload(self) -> dict[str, Any]:
        """Serialize for the provider, sending ``schema_`` under its ``schema`` alias."""
        return self.model_dump(by_alias=True, exclude_none=True)


class SeedUnderstandInput(BaseModel):
    """Input model for ``seed_understand``."""

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=32000,
        description="The question or task for the model to reason about.",
    )
    images: list[UnderstandingImageInput] | None = Field(
        None,
        max_length=32,
        description="Images to understand (URL or Base64). For local files, upload via media_upload first.",
    )
    videos: list[UnderstandingVideoInput] | None = Field(
        None,
        max_length=32,
        description="Videos to understand. Must be HTTPS URLs — video Base64 is not supported.",
    )
    system: str | None = Field(
        None,
        max_length=32000,
        description="Optional system instruction to guide the model's behavior.",
    )
    model: str | None = Field(
        None,
        description="Override the configured Seed 2.1 model ID. Must be present in the capability registry.",
    )
    thinking: bool | None = Field(
        None,
        description=(
            "Deprecated and ignored. Deep thinking is always enabled; use reasoning_effort to "
            "control its depth. The reasoning trace is never returned. Will be removed."
        ),
    )
    reasoning_effort: Literal["low", "medium", "high"] = Field(
        "medium",
        description=(
            "Depth of the model's deep thinking: 'low', 'medium' (default), or 'high'. "
            "Thinking always happens; this controls how much, trading latency and token cost."
        ),
    )
    response_format: UnderstandingResponseFormat | None = Field(
        None,
        description=(
            "Constrain the answer to JSON ('json_object') or to a JSON Schema ('json_schema', "
            "enforced by the provider during generation). The parsed result is returned in "
            "choices[].parsed."
        ),
    )
    json_retry: int = Field(
        0,
        ge=0,
        le=2,
        description=(
            "Extra attempts (0-2) when a JSON answer fails to parse or validate. Each retry is a "
            "new billed completion. Never retried when the answer was cut off by max_tokens "
            "(finish_reason='length'); raise max_tokens instead."
        ),
    )
    save_to: str | None = Field(
        None,
        description=(
            "Optional absolute file path (stdio transport only) where the final answer is written "
            "atomically: pretty-printed JSON when the answer parsed as JSON, otherwise UTF-8 text. "
            "Reasoning is never written. Must be inside an allowed output root (the client's MCP "
            "roots, or OUTPUT_ROOTS). Validated before the model is called."
        ),
    )
    overwrite: bool = Field(
        False,
        description=(
            "Allow save_to to replace an existing file with different content. An existing "
            "file with identical content is always accepted."
        ),
    )
    return_content: Literal["full", "summary", "none"] = Field(
        "full",
        description=(
            "How much of the answer to inline in the result: 'full' (default), 'summary' (first "
            "2,000 characters), or 'none' (metadata only; use with save_to). parsed is always "
            "returned when available."
        ),
    )
    temperature: float | None = Field(
        None,
        ge=0.0,
        le=2.0,
        description="Sampling temperature (0.0-2.0). Lower is more deterministic.",
    )
    max_tokens: int | None = Field(
        None,
        ge=1,
        le=32768,
        description=(
            "Maximum output tokens (1-32768), including deep-thinking tokens. Raise it if "
            "answers end with finish_reason='length'."
        ),
    )
    top_p: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Nucleus sampling probability (0.0-1.0).",
    )
    repetition_penalty: float | None = Field(
        None,
        ge=0.0,
        le=2.0,
        description="Repetition penalty (0.0-2.0). Ark-only parameter.",
    )


class SeedUnderstandOutput(BaseModel):
    """Output model for ``seed_understand``."""

    provider: Literal["byteplus-modelark"] = Field(
        "byteplus-modelark", description="Provider that generated the response."
    )
    model: str = Field(..., description="Model ID used for the completion.")
    completion_id: str | None = Field(
        None,
        description="Provider completion ID (e.g. 'chatcmpl-...') for tracing.",
    )
    choices: list[UnderstandingChoice] = Field(
        ..., description="Model completion choices (one for non-streaming)."
    )
    usage: UnderstandingUsage = Field(
        ..., description="Token usage summed over all attempts (including json_retry attempts)."
    )
    attempts: int = Field(
        1, description="Completions requested: 1 plus any json_retry attempts that were used."
    )
    saved_path: str | None = Field(
        None, description="Absolute path the answer was written to when save_to was set."
    )
    saved_bytes: int | None = Field(
        None, description="Size in bytes of the file written to saved_path."
    )
    request_id: str | None = Field(None, description="Provider request ID for support tracing.")


def _check_json(
    content: str,
    finish_reason: str,
    response_format: UnderstandingResponseFormat | None,
) -> tuple[dict[str, Any] | list[Any] | None, SchemaViolation | None]:
    """Parse and (for json_schema) validate the answer. Never raises."""
    if response_format is None or response_format.type == "text":
        return None, None
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        return None, SchemaViolation(
            path="", message=f"Answer is not valid JSON: {exc.msg}", finish_reason=finish_reason
        )
    if not isinstance(value, (dict, list)):
        return None, SchemaViolation(
            path="",
            message="Answer is JSON but not an object or array.",
            finish_reason=finish_reason,
        )
    if response_format.type == "json_schema" and response_format.json_schema is not None:
        validator = Draft202012Validator(response_format.json_schema.schema_)
        error = next(iter(sorted(validator.iter_errors(value), key=lambda e: list(e.path))), None)
        if error is not None:
            pointer = "".join(f"/{part}" for part in error.absolute_path)
            return None, SchemaViolation(
                path=pointer, message=error.message, finish_reason=finish_reason
            )
    return value, None


def _retry_prompt(prompt: str, violation: SchemaViolation) -> str:
    location = f" at {violation.path}" if violation.path else ""
    return (
        f"{prompt}\n\nYour previous answer was rejected{location}: {violation.message}. "
        "Reply again with only the corrected JSON."
    )


async def seed_understand(
    input: SeedUnderstandInput, ctx: Context
) -> SeedUnderstandOutput | ToolResult:
    """Understand images and videos, or reason about a task, through the Seed 2.1 multimodal model.

    Accepts a natural-language prompt plus optional images and videos, and
    returns only the model's final answer. The model always uses deep thinking
    (depth set by reasoning_effort, default 'medium'), but the reasoning trace is
    never returned. Use response_format to force JSON or a JSON Schema at
    generation time (parsed result in choices[].parsed), and save_to to write the
    answer straight to a local file. For local media files, upload them first with
    media_upload to obtain an HTTPS URL; video Base64 is not supported by the chat
    endpoint. This tool requires task-augmented execution so long video analysis
    does not consume a foreground MCP request deadline.
    """
    await context_log(ctx, "info", "Starting Seed 2.1 multimodal understanding")
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    if not settings.has_understanding:
        raise ValueError(
            "BYTEPLUS_MODELARK_API_KEY is not configured. Set it in .env to enable understanding tools."
        )

    registry = get_capability_registry()
    caps = registry.get_understanding_capabilities(input.model)

    image_count = len(input.images or [])
    video_count = len(input.videos or [])
    total_media = image_count + video_count
    if total_media > caps.max_media_parts:
        raise ValueError(
            f"Model '{caps.model_id}' supports at most {caps.max_media_parts} "
            f"media parts, got {total_media}."
        )

    if input.thinking is False:
        log_warning("thinking_flag_ignored", model=caps.model_id)
        await context_log(
            ctx, "warning", "thinking=false is deprecated and ignored; deep thinking is always on."
        )

    if caps.supports_thinking and input.reasoning_effort not in caps.reasoning_efforts:
        raise ValueError(
            f"Model '{caps.model_id}' supports reasoning_effort values "
            f"{caps.reasoning_efforts}, got '{input.reasoning_effort}'."
        )

    if input.response_format and input.response_format.type not in caps.response_formats:
        raise ValueError(
            f"Model '{caps.model_id}' does not support response_format "
            f"'{input.response_format.type}'; supported: {caps.response_formats}."
        )

    # Validate save_to before any billable call.
    save_target = await validate_output_target(
        input.save_to, ctx=ctx, settings=settings, kind="file", field="save_to"
    )

    await ctx.report_progress(progress=30, total=100)

    image_parts = [src.model_dump() for src in input.images] if input.images else None
    video_parts = [src.model_dump() for src in input.videos] if input.videos else None
    response_format_payload = (
        input.response_format.provider_payload() if input.response_format else None
    )

    service = SeedUnderstandingService()
    prompt = input.prompt
    prompt_tokens = completion_tokens = total_tokens = 0
    reasoning_tokens: int | None = None
    attempts = 0
    response: ChatCompletionProviderResponse | None = None
    request_id: str | None = None
    checked: list[tuple[str, str, dict[str, Any] | list[Any] | None, SchemaViolation | None]] = []
    try:
        while True:
            attempts += 1
            request = SeedUnderstandingService.build_request(
                model=caps.model_id,
                prompt=prompt,
                image_parts=image_parts,
                video_parts=video_parts,
                system=input.system,
                thinking=caps.supports_thinking,
                reasoning_effort=input.reasoning_effort,
                temperature=input.temperature,
                max_tokens=input.max_tokens,
                top_p=input.top_p,
                repetition_penalty=input.repetition_penalty,
                response_format=response_format_payload,
            )
            estimated_cost = log_cost_estimate(
                product="understanding", variations=1, max_tokens=input.max_tokens
            )
            await ctx.report_progress(progress=50, total=100)
            try:
                async with billed_provider_slot(
                    ctx,
                    provider="modelark",
                    product="understanding",
                    estimated_cost_usd=estimated_cost,
                ):
                    attempt_response, request_id = await call_with_retry(
                        lambda request=request: service.generate(request),  # type: ignore[misc]
                        policy=_UNDERSTANDING_RETRY_POLICY,
                    )
                    response = attempt_response
            except ProviderError as exc:
                await context_log(ctx, "error", f"Understanding failed: {exc.message}")
                return provider_error_result(exc)

            usage = SeedUnderstandingService.extract_usage(attempt_response)
            prompt_tokens += usage.prompt_tokens
            completion_tokens += usage.completion_tokens
            total_tokens += usage.total_tokens
            details = usage.completion_tokens_details
            if details is not None and details.reasoning_tokens is not None:
                reasoning_tokens = (reasoning_tokens or 0) + details.reasoning_tokens

            # Keep only the final answer; the reasoning trace is dropped here and
            # never logged, persisted, saved, or returned.
            checked = []
            for choice in attempt_response.choices:
                content = choice.message.content or ""
                finish_reason = choice.finish_reason or "stop"
                parsed, violation = _check_json(content, finish_reason, input.response_format)
                checked.append((content, finish_reason, parsed, violation))

            first_violation = checked[0][3] if checked else None
            if (
                first_violation is None
                or first_violation.finish_reason == "length"
                or attempts > input.json_retry
            ):
                break
            log_warning(
                "understanding_schema_violation_retry",
                model=caps.model_id,
                attempt=attempts,
                path=first_violation.path,
            )
            prompt = _retry_prompt(input.prompt, first_violation)
    finally:
        await service.close()

    await ctx.report_progress(progress=80, total=100)
    if response is None:  # unreachable: the loop returns or raises before this
        raise RuntimeError("Understanding completed without a provider response.")

    choices: list[UnderstandingChoice] = []
    for content, finish_reason, parsed, violation in checked:
        if violation is not None:
            log_warning(
                "understanding_schema_violation",
                model=caps.model_id,
                path=violation.path,
                finish_reason=finish_reason,
            )
        if input.return_content == "none":
            inline = ""
        elif input.return_content == "summary":
            inline = content[:_SUMMARY_CHARS]
        else:
            inline = content
        choices.append(
            UnderstandingChoice(
                content=inline,
                content_chars=len(content),
                content_truncated=len(inline) < len(content),
                parsed=parsed,
                schema_violation=violation,
                finish_reason=finish_reason,
            )
        )

    saved_path: str | None = None
    saved_bytes: int | None = None
    if save_target is not None and checked:
        target, roots = save_target
        content, _finish, parsed, _violation = checked[0]
        payload = (
            json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
            if parsed is not None
            else content
        ).encode("utf-8")
        try:
            outcome = write_output_file(target, payload, roots=roots, overwrite=input.overwrite)
        except (OutputPathError, OSError) as exc:
            # The completion was billed; keep the answer inline rather than failing.
            await context_log(ctx, "warning", f"save_to failed: {exc}")
            if input.return_content != "full" and choices:
                choices[0] = choices[0].model_copy(
                    update={"content": content, "content_truncated": False}
                )
        else:
            saved_path = str(outcome.path)
            saved_bytes = outcome.bytes

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "understanding_complete",
        model=caps.model_id,
        choices=len(choices),
        attempts=attempts,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        request_id=request_id,
    )

    return SeedUnderstandOutput(
        model=caps.model_id,
        completion_id=SeedUnderstandingService.extract_completion_id(response),
        choices=choices,
        usage=UnderstandingUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
        ),
        attempts=attempts,
        saved_path=saved_path,
        saved_bytes=saved_bytes,
        request_id=request_id,
    )


# save_to writes local files, so the tool is not read-only.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
