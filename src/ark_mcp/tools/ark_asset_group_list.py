"""``ark_asset_group_list`` tool — list asset groups or read them by ID."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.assets import AssetGroup
from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetGroupListInput, AssetGroupPage
from ark_mcp.tools._asset_shared import asset_call, lookup_each, retrying
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_group_list(
    input: AssetGroupListInput, ctx: Context
) -> AssetGroupPage | ToolResult:
    """List private asset groups by fuzzy name and type, or read up to 20 by group_ids.

    group_type 'AIGC' = virtual portraits, 'LivenessFace' = verified real people. Only groups
    owned by this account are visible. Requires AK/SK.
    """
    group_ids = input.group_ids

    async def _list(service: AssetService) -> AssetGroupPage:
        if group_ids:
            groups, errors = await lookup_each(
                group_ids, lambda group_id: service.get_group(group_id, input.project_name)
            )
            return AssetGroupPage(groups=groups, errors=errors)
        groups_page: tuple[list[AssetGroup], str | None] = await retrying(
            lambda: service.list_groups(
                name=input.name,
                group_ids=None,
                group_type=input.group_type,
                max_results=input.max_results,
                next_token=input.next_token,
                sort_order=input.sort_order,
                project_name=input.project_name,
            )
        )
        return AssetGroupPage(groups=groups_page[0], next_token=groups_page[1])

    try:
        return await asset_call(ctx, _list)
    except ProviderError as exc:
        return provider_error_result(exc)


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
