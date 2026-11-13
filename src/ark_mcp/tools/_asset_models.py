"""Input and output models shared by the ``ark_asset_*`` tools.

All fields carry descriptions because these models are MCP tool schemas.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
    model_validator,
)

from ark_mcp.domain.assets import Asset, AssetGroup, validate_asset_id
from ark_mcp.tools.media_presign import validate_object_key

_PROJECT_DESCRIPTION = (
    "ModelArk project name; defaults to BYTEPLUS_MODELARK_PROJECT_NAME. Assets only work "
    "within one project, so keep it consistent."
)
_GROUP_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
MAX_LOOKUP_IDS = 20


def project_name_field() -> Any:
    return Field(None, min_length=1, max_length=64, description=_PROJECT_DESCRIPTION)


def _strip_asset_scheme(value: object) -> object:
    if isinstance(value, str) and value.lower().startswith("asset://"):
        return value[len("asset://") :]
    return value


GroupId = Annotated[str, StringConstraints(pattern=_GROUP_ID_PATTERN)]
AssetId = Annotated[str, BeforeValidator(_strip_asset_scheme), AfterValidator(validate_asset_id)]


class AssetLookupError(BaseModel):
    """An asset or group ID that could not be read."""

    id: str = Field(..., description="The asset or group ID that failed.")
    code: str = Field(..., description="Provider error code (e.g. NotFound.Asset).")
    message: str = Field(..., description="Human-readable reason.")


# --- Groups --------------------------------------------------------------------


class AssetGroupEnsureInput(BaseModel):
    """Input for ``ark_asset_group_ensure``."""

    subject: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Exact subject name; reuses the single AIGC group with this name or creates it.",
    )
    description: str | None = Field(
        None, max_length=300, description="Used only when a new group is created."
    )
    project_name: str | None = project_name_field()


class AssetGroupResult(BaseModel):
    """Output for tools that return one asset group."""

    group: AssetGroup = Field(..., description="The asset group.")
    created: bool = Field(
        False, description="True when this call created the group; false when it already existed."
    )


class AssetGroupListInput(BaseModel):
    """Input for ``ark_asset_group_list``."""

    group_ids: list[GroupId] | None = Field(
        None,
        min_length=1,
        max_length=MAX_LOOKUP_IDS,
        description=(
            "Read these groups (1-20 IDs, any type) instead of listing. Exclusive with name, "
            "next_token, sort_order."
        ),
    )
    name: str | None = Field(None, max_length=64, description="Fuzzy group-name filter.")
    group_type: Literal["AIGC", "LivenessFace"] = Field(
        "AIGC",
        description=(
            "'AIGC' (virtual portrait) or 'LivenessFace' (verified real person). Ignored with "
            "group_ids."
        ),
    )
    max_results: int = Field(
        20, ge=1, le=100, description="Page size (1-100). Ignored with group_ids."
    )
    next_token: str | None = Field(
        None, max_length=4096, description="next_token from the previous page."
    )
    sort_order: Literal["Asc", "Desc"] | None = Field(
        None, description="Creation-time order; provider default when omitted."
    )
    project_name: str | None = project_name_field()

    @model_validator(mode="after")
    def _ids_or_filters(self) -> AssetGroupListInput:
        if self.group_ids and (self.name or self.next_token or self.sort_order):
            raise ValueError("group_ids cannot be combined with name, next_token or sort_order.")
        return self


class AssetGroupPage(BaseModel):
    """Output for ``ark_asset_group_list``."""

    groups: list[AssetGroup] = Field(..., description="Asset groups on this page.")
    next_token: str | None = Field(
        None, description="Pass as next_token for the next page; null on the last page."
    )
    errors: list[AssetLookupError] = Field(
        default_factory=list, description="group_ids that could not be read."
    )


# --- Assets --------------------------------------------------------------------


class AssetSource(BaseModel):
    """One file to register as an asset: a public HTTPS URL or a media_upload object key."""

    url: str | None = Field(
        None,
        description="Public HTTPS URL the provider downloads. Exactly one of url or object_key.",
    )
    object_key: str | None = Field(
        None,
        description="media_upload object_key (presigned automatically). Exactly one of url or object_key.",
    )
    asset_type: Literal["Image", "Video", "Audio"] | None = Field(
        None,
        description=(
            "Inferred from the extension or object key when omitted. Image: 300-6000 px, "
            "<30 MB. Video: mp4/mov, 2-30 s, <=200 MB. Audio: wav/mp3, 2-30 s, <=15 MB."
        ),
    )
    name: str | None = Field(
        None,
        max_length=64,
        description="Search label for ark_asset_list; the model never sees it.",
    )

    @model_validator(mode="after")
    def _one_source(self) -> AssetSource:
        if bool(self.url) == bool(self.object_key):
            raise ValueError("Provide exactly one of url or object_key.")
        if self.object_key:
            validate_object_key(self.object_key)
        return self


class AssetCreateInput(BaseModel):
    """Input for ``ark_asset_create``."""

    sources: list[AssetSource] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Files (1-20) of ONE subject; use separate calls for different people or characters.",
    )
    group_id: str | None = Field(
        None,
        pattern=_GROUP_ID_PATTERN,
        description=(
            "Target group ID. Exactly one of group_id or subject. Real people: use the "
            "group_id from ark_asset_verification_result."
        ),
    )
    subject: str | None = Field(
        None,
        min_length=1,
        max_length=64,
        description=(
            "AIGC subject name: reuses or creates the group with this exact name. Exactly one "
            "of group_id or subject."
        ),
    )
    skip_moderation: bool = Field(
        False,
        description=(
            "Skip non-baseline content pre-filter checks; works only after the pre-filter is "
            "turned off in the ModelArk console."
        ),
    )
    wait_until_active: bool = Field(
        True,
        description="Wait until each asset is Active or Failed, up to wait_timeout_seconds.",
    )
    wait_timeout_seconds: int = Field(
        120, ge=5, le=900, description="Max seconds to wait when wait_until_active is true."
    )
    project_name: str | None = project_name_field()

    @model_validator(mode="after")
    def _one_target(self) -> AssetCreateInput:
        if bool(self.group_id) == bool(self.subject):
            raise ValueError("Provide exactly one of group_id or subject.")
        return self


class AssetCreateItem(BaseModel):
    """Per-source result of ``ark_asset_create``."""

    index: int = Field(..., description="Zero-based position of the source in the request.")
    asset_id: str | None = Field(None, description="Created asset ID; null if not created.")
    asset_uri: str | None = Field(
        None, description="'asset://<asset_id>' reference for Seedance; null if not created."
    )
    status: str | None = Field(
        None, description="Last status: Processing, Active, or Failed; null if not created."
    )
    error: str | None = Field(None, description="Why this source failed, if it did.")


class AssetCreateOutput(BaseModel):
    """Output for ``ark_asset_create``."""

    group_id: str = Field(..., description="Group the assets were added to.")
    group_created: bool = Field(False, description="True when this call created the group.")
    group_created_empty: bool = Field(
        False,
        description=(
            "True when this call created the group but every source failed; delete the empty "
            "group with ark_asset_delete (when registered) if unneeded."
        ),
    )
    items: list[AssetCreateItem] = Field(..., description="One result per source, in order.")
    all_active: bool = Field(..., description="True when every source ended Active.")


class AssetListInput(BaseModel):
    """Input for ``ark_asset_list``."""

    asset_ids: list[AssetId] | None = Field(
        None,
        min_length=1,
        max_length=MAX_LOOKUP_IDS,
        description=(
            "Read these assets (1-20 IDs or asset:// references) instead of listing. Exclusive "
            "with the filters."
        ),
    )
    group_ids: list[GroupId] | None = Field(
        None, max_length=100, description="Only assets in these groups."
    )
    group_type: Literal["AIGC", "LivenessFace"] = Field(
        "AIGC",
        description="Only assets in groups of this type. Ignored with asset_ids.",
    )
    statuses: list[Literal["Processing", "Active", "Failed"]] | None = Field(
        None, description="Only assets with these statuses."
    )
    name: str | None = Field(None, max_length=64, description="Fuzzy asset-name filter.")
    max_results: int = Field(
        20, ge=1, le=100, description="Page size (1-100). Ignored with asset_ids."
    )
    next_token: str | None = Field(
        None, max_length=4096, description="next_token from the previous page."
    )
    sort_order: Literal["Asc", "Desc"] | None = Field(
        None, description="Creation-time order; provider default when omitted."
    )
    project_name: str | None = project_name_field()

    @model_validator(mode="after")
    def _ids_or_filters(self) -> AssetListInput:
        filters = (self.group_ids, self.statuses, self.name, self.next_token, self.sort_order)
        if self.asset_ids and any(filters):
            raise ValueError(
                "asset_ids cannot be combined with group_ids, statuses, name, next_token or "
                "sort_order."
            )
        return self


class AssetPage(BaseModel):
    """Output for ``ark_asset_list``."""

    assets: list[Asset] = Field(..., description="Assets on this page.")
    next_token: str | None = Field(
        None, description="Pass as next_token for the next page; null on the last page."
    )
    errors: list[AssetLookupError] = Field(
        default_factory=list, description="asset_ids that could not be read."
    )


class _AssetOrGroupTarget(BaseModel):
    asset_id: AssetId | None = Field(
        None,
        description="Asset ID or asset:// reference. Exactly one of asset_id or group_id.",
    )
    group_id: GroupId | None = Field(
        None, description="Asset group ID. Exactly one of asset_id or group_id."
    )
    project_name: str | None = project_name_field()

    @model_validator(mode="after")
    def _one_target(self) -> _AssetOrGroupTarget:
        if bool(self.asset_id) == bool(self.group_id):
            raise ValueError("Provide exactly one of asset_id or group_id.")
        return self


class AssetUpdateInput(_AssetOrGroupTarget):
    """Input for ``ark_asset_update``."""

    name: str | None = Field(
        None, min_length=1, max_length=64, description="New asset or group name."
    )
    description: str | None = Field(
        None, max_length=300, description="New group description (group_id only)."
    )

    @model_validator(mode="after")
    def _require_change(self) -> AssetUpdateInput:
        if self.description is not None and self.asset_id:
            raise ValueError("description can only be updated on a group (group_id).")
        if self.name is None and self.description is None:
            raise ValueError("Provide name and/or description to update.")
        return self


class AssetUpdateResult(BaseModel):
    """Output for ``ark_asset_update``."""

    asset: Asset | None = Field(None, description="The updated asset, when asset_id was given.")
    group: AssetGroup | None = Field(
        None, description="The updated group, when group_id was given."
    )


class AssetDeleteInput(_AssetOrGroupTarget):
    """Input for ``ark_asset_delete``."""

    confirm: Literal[True] = Field(..., description="Must be true; deletion is irreversible.")
    cascade: bool = Field(
        False,
        description="Group deletes only: also delete its assets. Non-empty groups are refused without it.",
    )


class AssetDeleteResult(BaseModel):
    """Output for ``ark_asset_delete``."""

    id: str = Field(..., description="ID of the deleted asset or group.")
    kind: Literal["asset", "group"] = Field(..., description="Whether an asset or a group.")
    deleted: bool = Field(..., description="True when the provider accepted the delete.")


# --- Real-person verification -------------------------------------------------


class VerificationStartInput(BaseModel):
    """Input for ``ark_asset_verification_start``."""

    callback_url: str | None = Field(
        None,
        description=(
            "HTTPS redirect page after verification (receives bytedToken and resultCode; "
            "10000 = success). Defaults to BYTEPLUS_MODELARK_ASSET_VERIFY_CALLBACK_URL."
        ),
    )
    language: Literal["en", "zh", "zh-Hant"] = Field(
        "en", description="Language of the verification page."
    )
    project_name: str | None = project_name_field()


class VerificationStartOutput(BaseModel):
    """Output for ``ark_asset_verification_start``."""

    h5_link: str = Field(
        ...,
        description=(
            "Verification page link with temporary credentials: share only with the person "
            "being verified. Invalid after a failed attempt."
        ),
    )
    verification_token: str = Field(
        ..., description="Token for ark_asset_verification_result; valid about 30 minutes."
    )
    expires_at: str = Field(..., description="Approximate token expiry (ISO-8601, UTC).")
    next_step: str = Field(..., description="What to do next.")


class VerificationResultInput(BaseModel):
    """Input for ``ark_asset_verification_result``."""

    verification_token: str = Field(
        ...,
        min_length=8,
        max_length=512,
        description="verification_token from ark_asset_verification_start.",
    )
    wait_seconds: int = Field(
        0,
        ge=0,
        le=600,
        description="Seconds to keep polling for completion (0 = single check).",
    )
    project_name: str | None = project_name_field()


class VerificationResultOutput(BaseModel):
    """Output for ``ark_asset_verification_result``."""

    status: Literal["verified", "pending"] = Field(
        ..., description="'verified' once the group exists; otherwise 'pending'."
    )
    group_id: str | None = Field(
        None,
        description="Verified person's LivenessFace group ID, for ark_asset_create(group_id=...).",
    )
    message: str = Field(..., description="Human-readable status detail.")
