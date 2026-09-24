"""``ark_asset_update`` tool — rename an asset."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.assets import Asset
from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetResult, AssetUpdateInput
from ark_mcp.tools._asset_shared import asset_call, retrying
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_update(input: AssetUpdateInput, ctx: Context) -> AssetResult | ToolResult:
    """Rename an asset (the name is for search only; the model never sees it).

    Returns the updated asset. Requires BytePlus AK/SK.
    """

    async def _update(service: AssetService) -> Asset:
        await service.update_asset(input.asset_id, name=input.name, project_name=input.project_name)
        return await retrying(lambda: service.get_asset(input.asset_id, input.project_name))

    try:
        asset = await asset_call(ctx, _update)
    except ProviderError as exc:
        return provider_error_result(exc)
    return AssetResult(asset=asset)


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
