"""``ark_asset_group_ensure`` tool — reuse or create the AIGC group for one subject."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.assets import AssetGroup
from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetGroupEnsureInput, AssetGroupResult
from ark_mcp.tools._asset_shared import asset_call, resolve_subject_group
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_group_ensure(
    input: AssetGroupEnsureInput, ctx: Context
) -> AssetGroupResult | ToolResult:
    """Return the single AIGC asset group named exactly ``subject``, creating it if absent.

    Fails with ambiguous_asset_group (listing IDs) when several groups share the name.
    Creation is serialized per server process; across servers, use a pre-created group ID.
    Real people use ark_asset_verification_start instead. Requires AK/SK.
    """

    async def _ensure(service: AssetService) -> tuple[AssetGroup, bool]:
        return await resolve_subject_group(
            service,
            subject=input.subject,
            description=input.description,
            project_name=input.project_name,
            create_if_missing=True,
        )

    try:
        group, created = await asset_call(ctx, _ensure)
    except ProviderError as exc:
        return provider_error_result(exc)
    return AssetGroupResult(group=group, created=created)


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
