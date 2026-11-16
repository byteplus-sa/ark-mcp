"""Moderation lookup defined in specs/SPEC_MODELARK_MODERATION_CONTRACT.md."""

from __future__ import annotations

import math
import unicodedata

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.moderation import ModerationIdType, ModerationResult
from ark_mcp.providers.modelark.moderation import ModerationService
from ark_mcp.providers.modelark.openapi import ModelArkOpenApiGateway
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.tools._errors import provider_error_result


class ModerationGetInput(BaseModel):
    """One explicit provider-record lookup."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        ...,
        strict=True,
        min_length=1,
        max_length=256,
        description="Bare provider task, asset, or inference request ID; not an Ark job ID or asset:// URI.",
    )
    type: ModerationIdType = Field(
        ...,
        description="ID type: task_id, asset_id, or request_id. Seedance 2.5 request_id lookup is unsupported; use task_id.",
    )

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if ":" in value or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in value
        ):
            raise ValueError(
                "Use a bare provider ID without schemes, whitespace, or control characters."
            )
        return value


async def ark_moderation_get(
    input: ModerationGetInput, ctx: Context
) -> ModerationResult | ToolResult:
    """Look up media block reasons for Seedance 2.0/2.5 or material assets.

    Requires AK/SK, Advanced Creation Rights, whitelist activation and IAM GetModerationResult.
    Trusted local/loopback access, account-wide and not tenant-isolated. Shared HTTP keys
    lack moderation:read. Records: within 14 days and after whitelist activation.
    Images/video/audio, not text. Seedance 2.5: use task_id, not request_id.
    No automatic retries or background jobs. Ten queries/minute per server runtime.
    Not found or empty reasons never establish approval or safety.
    """
    runtime = get_runtime(ctx)
    if not runtime.settings.has_modelark_moderation:
        raise ValueError(
            "Set BYTEPLUS_MODELARK_MODERATION_ENABLED=true and complete BYTEPLUS_MODELARK_ACCESS_KEY "
            "/ BYTEPLUS_MODELARK_SECRET_KEY for a whitelisted account."
        )
    owner = get_principal(ctx)
    async with runtime.provider_limiters.acquire("modelark-openapi", owner):
        delay = await runtime.moderation_query_limiter.try_reserve()
        if delay > 0:
            return ToolResult(
                content=f"LOCAL_RATE_LIMITED: No provider request was sent. Retry after {math.ceil(delay)} seconds.",
                is_error=True,
            )
        service = ModerationService(ModelArkOpenApiGateway(settings=runtime.settings))
        try:
            return await service.get_result(id=input.id, type=input.type)
        except ProviderError as exc:
            message = exc.message
            if exc.code == "NotFound.Id":
                message += (
                    " The ID may be invalid, unblocked, older than 14 days, or created before "
                    "whitelist activation. Text and Seedance 2.5 request-ID lookups are unsupported. "
                    "Not found does not establish approval or safety."
                )
            if exc.error.retry_after_seconds is not None:
                message += f" Retry after {math.ceil(exc.error.retry_after_seconds)} seconds."
            return provider_error_result(
                ProviderError(exc.error.model_copy(update={"message": message}))
            )
        finally:
            await service.close()


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
