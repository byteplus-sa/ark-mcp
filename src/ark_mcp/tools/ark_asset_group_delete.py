"""``ark_asset_group_delete`` tool — irreversibly delete an asset group."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetGroupDeleteInput, DeleteOutput
from ark_mcp.tools._asset_shared import asset_call
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_group_delete(
    input: AssetGroupDeleteInput, ctx: Context
) -> DeleteOutput | ToolResult:
    """Permanently delete an asset group. Irreversible; requires confirm=true.

    Its assets can no longer be used in generation. Real-person (LivenessFace)
    groups must also meet the provider's authorization-status rules for deletion.
    Only registered when BYTEPLUS_MODELARK_ASSETS_ALLOW_DELETE=true.
    """

    async def _delete(service: AssetService) -> None:
        await service.delete_group(input.group_id, input.project_name)

    try:
        await asset_call(ctx, _delete)
    except ProviderError as exc:
        return provider_error_result(exc)
    log_info("asset_group_deleted", group_id=input.group_id)
    return DeleteOutput(id=input.group_id, deleted=True)


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}
