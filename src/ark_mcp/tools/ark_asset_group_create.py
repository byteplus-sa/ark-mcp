"""``ark_asset_group_create`` tool — create a virtual-portrait (AIGC) asset group."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.config.env import get_settings
from ark_mcp.domain.assets import AssetGroup
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetGroupCreateInput, AssetGroupResult
from ark_mcp.tools._asset_shared import asset_call
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_group_create(
    input: AssetGroupCreateInput, ctx: Context
) -> AssetGroupResult | ToolResult:
    """Create a private virtual-portrait asset group (GroupType AIGC) in ModelArk.

    One group holds one subject: a fictional character, mascot, or product.
    AIGC assets must not resemble any real person; real people get their group
    from real-person verification (ark_asset_verification_start). Always creates
    a new group; prefer ark_asset_group_ensure to reuse a group by subject name.
    Requires Dreamina Seedance Advanced Creation Rights and BytePlus AK/SK.
    """

    async def _create(service: AssetService) -> str:
        return await service.create_group(
            name=input.name, description=input.description, project_name=input.project_name
        )

    try:
        group_id = await asset_call(ctx, _create)
    except ProviderError as exc:
        return provider_error_result(exc)
    log_info("asset_group_created", group_id=group_id)
    return AssetGroupResult(
        group=AssetGroup(
            group_id=group_id,
            name=input.name,
            description=input.description or "",
            group_type="AIGC",
            project_name=input.project_name or get_settings().modelark_project_name,
        ),
        created=True,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
