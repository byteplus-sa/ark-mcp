"""``ark_asset_update`` tool — rename an asset, or rename and re-describe a group."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetUpdateInput, AssetUpdateResult
from ark_mcp.tools._asset_shared import asset_call, retrying
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_update(input: AssetUpdateInput, ctx: Context) -> AssetUpdateResult | ToolResult:
    """Rename an asset (asset_id), or update a group's name and/or description (group_id).

    Pass exactly one of asset_id or group_id. Only resources owned by this account can be
    updated. Requires AK/SK.
    """
    asset_id, group_id = input.asset_id, input.group_id

    async def _update(service: AssetService) -> AssetUpdateResult:
        if asset_id:
            await service.update_asset(
                asset_id, name=input.name or "", project_name=input.project_name
            )
            asset = await retrying(lambda: service.get_asset(asset_id, input.project_name))
            return AssetUpdateResult(asset=asset)
        target = group_id or ""
        await service.update_group(
            target,
            name=input.name,
            description=input.description,
            project_name=input.project_name,
        )
        group = await retrying(lambda: service.get_group(target, input.project_name))
        return AssetUpdateResult(group=group)

    try:
        return await asset_call(ctx, _update)
    except ProviderError as exc:
        return provider_error_result(exc)


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
