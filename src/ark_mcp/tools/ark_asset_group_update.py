"""``ark_asset_group_update`` tool — rename or re-describe an asset group."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.assets import AssetGroup
from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetGroupResult, AssetGroupUpdateInput
from ark_mcp.tools._asset_shared import asset_call, retrying
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_group_update(
    input: AssetGroupUpdateInput, ctx: Context
) -> AssetGroupResult | ToolResult:
    """Update an asset group's name and/or description, then return the updated group.

    Only groups owned by this BytePlus account can be updated. Requires BytePlus AK/SK.
    """

    async def _update(service: AssetService) -> AssetGroup:
        await service.update_group(
            input.group_id,
            name=input.name,
            description=input.description,
            project_name=input.project_name,
        )
        return await retrying(lambda: service.get_group(input.group_id, input.project_name))

    try:
        group = await asset_call(ctx, _update)
    except ProviderError as exc:
        return provider_error_result(exc)
    return AssetGroupResult(group=group, created=False)


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
