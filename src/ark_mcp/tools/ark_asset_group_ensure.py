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

    Keeps all assets of one subject (a fictional character, mascot, or product)
    in the same group. Fails with ambiguous_asset_group when several AIGC groups
    share the name, listing their IDs. Subject creation is serialized within
    one server event loop; separate servers should use a pre-created group ID.
    Real people use LivenessFace groups from ark_asset_verification_result
    instead. Requires BytePlus AK/SK.
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
    "idempotentHint": False,
    "openWorldHint": True,
}
