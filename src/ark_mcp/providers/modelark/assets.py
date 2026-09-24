"""Private asset library service — typed wrappers over ModelArk OpenAPI actions.

Each method maps one documented action (service ``ark``, version
``2024-01-01``) to domain models. Field names follow the BytePlus docs:
https://docs.byteplus.com/en/docs/ModelArk/2333565 (virtual portrait) and
https://docs.byteplus.com/en/docs/ModelArk/2333589 (real-human).
"""

from __future__ import annotations

from typing import Any, Literal

from ark_mcp.config.env import get_settings
from ark_mcp.domain.assets import Asset, AssetGroup
from ark_mcp.providers.modelark.openapi import CREATE_ASSET_LIMITER, ModelArkOpenApiGateway

AssetTypeName = Literal["Image", "Video", "Audio"]


def _drop_none(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if value is not None}


class AssetService:
    """Service layer for asset groups, assets, and real-person verification."""

    def __init__(self, gateway: ModelArkOpenApiGateway | None = None) -> None:
        self._gateway = gateway or ModelArkOpenApiGateway()
        self._default_project = get_settings().modelark_project_name

    def _project(self, project_name: str | None) -> str:
        return project_name or self._default_project

    async def close(self) -> None:
        await self._gateway.close()

    # --- Asset groups ----------------------------------------------------

    async def create_group(
        self,
        *,
        name: str,
        description: str | None,
        project_name: str | None,
    ) -> str:
        """``CreateAssetGroup`` (virtual portrait / ``AIGC``). Returns the group ID."""
        result = await self._gateway.call(
            "CreateAssetGroup",
            _drop_none(
                {
                    "Name": name,
                    "Description": description,
                    "GroupType": "AIGC",
                    "ProjectName": self._project(project_name),
                }
            ),
        )
        return str(result.get("Id") or "")

    async def get_group(self, group_id: str, project_name: str | None) -> AssetGroup:
        result = await self._gateway.call(
            "GetAssetGroup", {"Id": group_id, "ProjectName": self._project(project_name)}
        )
        return AssetGroup.from_provider(result)

    async def list_groups(
        self,
        *,
        name: str | None,
        group_ids: list[str] | None,
        group_type: str,
        max_results: int,
        next_token: str | None,
        sort_order: str | None,
        project_name: str | None,
    ) -> tuple[list[AssetGroup], str | None]:
        # The provider rejects a missing Filter (MissingParameter.Filter).
        filter_body = _drop_none(
            {"Name": name, "GroupIds": group_ids or None, "GroupType": group_type}
        )
        result = await self._gateway.call(
            "ListAssetGroups",
            _drop_none(
                {
                    "Filter": filter_body,
                    "MaxResults": max_results,
                    "NextToken": next_token,
                    "SortBy": "CreateTime" if sort_order else None,
                    "SortOrder": sort_order,
                    "ProjectName": self._project(project_name),
                }
            ),
        )
        items = result.get("Items") or []
        groups = [AssetGroup.from_provider(item) for item in items if isinstance(item, dict)]
        return groups, (str(result["NextToken"]) if result.get("NextToken") else None)

    async def update_group(
        self,
        group_id: str,
        *,
        name: str | None,
        description: str | None,
        project_name: str | None,
    ) -> None:
        await self._gateway.call(
            "UpdateAssetGroup",
            _drop_none(
                {
                    "Id": group_id,
                    "Name": name,
                    "Description": description,
                    "ProjectName": self._project(project_name),
                }
            ),
        )

    async def delete_group(self, group_id: str, project_name: str | None) -> None:
        await self._gateway.call(
            "DeleteAssetGroup", {"Id": group_id, "ProjectName": self._project(project_name)}
        )

    # --- Assets ------------------------------------------------------------

    async def create_asset(
        self,
        *,
        group_id: str,
        url: str,
        asset_type: AssetTypeName,
        name: str | None,
        skip_moderation: bool,
        project_name: str | None,
    ) -> str:
        """``CreateAsset`` (asynchronous). Returns the asset ID; poll ``get_asset``."""
        await CREATE_ASSET_LIMITER.wait(get_settings().modelark_asset_create_qpm)
        result = await self._gateway.call(
            "CreateAsset",
            _drop_none(
                {
                    "GroupId": group_id,
                    "URL": url,
                    "AssetType": asset_type,
                    "Name": name,
                    "Moderation": {"Strategy": "Skip"} if skip_moderation else None,
                    "ProjectName": self._project(project_name),
                }
            ),
        )
        return str(result.get("Id") or "")

    async def get_asset(self, asset_id: str, project_name: str | None) -> Asset:
        result = await self._gateway.call(
            "GetAsset", {"Id": asset_id, "ProjectName": self._project(project_name)}
        )
        if not result.get("Id"):
            result = {**result, "Id": asset_id}
        return Asset.from_provider(result)

    async def list_assets(
        self,
        *,
        group_ids: list[str] | None,
        group_type: str,
        statuses: list[str] | None,
        name: str | None,
        max_results: int,
        next_token: str | None,
        sort_order: str | None,
        project_name: str | None,
    ) -> tuple[list[Asset], str | None]:
        filter_body = _drop_none(
            {
                "GroupIds": group_ids or None,
                "GroupType": group_type,
                "Statuses": statuses or None,
                "Name": name,
            }
        )
        result = await self._gateway.call(
            "ListAssets",
            _drop_none(
                {
                    "Filter": filter_body,
                    "MaxResults": max_results,
                    "NextToken": next_token,
                    "SortBy": "CreateTime" if sort_order else None,
                    "SortOrder": sort_order,
                    "ProjectName": self._project(project_name),
                }
            ),
        )
        items = result.get("Items") or []
        assets = [Asset.from_provider(item) for item in items if isinstance(item, dict)]
        return assets, (str(result["NextToken"]) if result.get("NextToken") else None)

    async def update_asset(self, asset_id: str, *, name: str, project_name: str | None) -> None:
        await self._gateway.call(
            "UpdateAsset",
            {"Id": asset_id, "Name": name, "ProjectName": self._project(project_name)},
        )

    async def delete_asset(self, asset_id: str, project_name: str | None) -> None:
        await self._gateway.call(
            "DeleteAsset", {"Id": asset_id, "ProjectName": self._project(project_name)}
        )

    # --- Real-person verification -----------------------------------------

    async def create_verification_session(
        self, *, callback_url: str, project_name: str | None
    ) -> dict[str, str]:
        """``CreateVisualValidateSession``. Returns ``BytedToken`` and ``H5Link``."""
        result = await self._gateway.call(
            "CreateVisualValidateSession",
            {"CallbackURL": callback_url, "ProjectName": self._project(project_name)},
        )
        return {
            "byted_token": str(result.get("BytedToken") or ""),
            "h5_link": str(result.get("H5Link") or ""),
        }

    async def get_verification_result(
        self, *, byted_token: str, project_name: str | None
    ) -> str | None:
        """``GetVisualValidateResult``. Returns the new ``LivenessFace`` group ID, if any."""
        result = await self._gateway.call(
            "GetVisualValidateResult",
            {"BytedToken": byted_token, "ProjectName": self._project(project_name)},
        )
        group_id = result.get("GroupId")
        return str(group_id) if group_id else None
