"""Signed lookup adapter for specs/SPEC_MODELARK_MODERATION_CONTRACT.md."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.domain.moderation import ModerationBlockReason, ModerationIdType, ModerationResult
from ark_mcp.providers.modelark.openapi import ModelArkOpenApiGateway


class _ModerationResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    block_reasons: list[ModerationBlockReason]


class ModerationService:
    """Query existing moderation records without automatic retries."""

    def __init__(self, gateway: ModelArkOpenApiGateway | None = None) -> None:
        self._gateway = gateway or ModelArkOpenApiGateway()

    async def get_result(self, *, id: str, type: ModerationIdType) -> ModerationResult:
        """Look up a task, asset, or inference request using signed OpenAPI."""
        result = await self._gateway.call("GetModerationResult", {"Id": id, "Type": type})
        try:
            parsed = _ModerationResponse.model_validate(result)
        except ValidationError:
            raise ProviderError(
                NormalizedProviderError(
                    provider="modelark-openapi",
                    operation="GetModerationResult",
                    code="INVALID_RESPONSE",
                    message="ModelArk returned an invalid moderation result; no safety verdict is available.",
                    retryable=False,
                )
            ) from None
        return ModerationResult(id=id, type=type, block_reasons=parsed.block_reasons)

    async def close(self) -> None:
        """Release the per-call HTTP client."""
        await self._gateway.close()
