"""``ark_asset_group_get`` tool — read one asset group."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.assets import AssetGroup
from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetGroupGetInput, AssetGroupResult
from ark_mcp.tools._asset_shared import asset_call, retrying
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_group_get(
    input: AssetGroupGetInput, ctx: Context
) -> AssetGroupResult | ToolResult:
    """Get one private asset group (name, description, type AIGC or LivenessFace, project).

    Only groups owned by this BytePlus account are visible; groups authorized
    from another account cannot be read. Requires BytePlus AK/SK.
    """

    async def _get(service: AssetService) -> AssetGroup:
        return await retrying(lambda: service.get_group(input.group_id, input.project_name))

    try:
        group = await asset_call(ctx, _get)
    except ProviderError as exc:
        return provider_error_result(exc)
    return AssetGroupResult(group=group, created=False)


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
