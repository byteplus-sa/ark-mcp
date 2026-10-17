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
    "ModelArk project name. Defaults to BYTEPLUS_MODELARK_PROJECT_NAME ('default'). Assets "
    "only work with inference endpoints in the same project; keep one project throughout."
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
        description=(
            "Exact subject name. Reuses the single AIGC group with exactly this name, or "
            "creates it within one server event loop. Separate servers should use a pre-created "
            "group ID to avoid concurrent duplicate creation."
        ),
    )
    description: str | None = Field(
        None, max_length=300, description="Description used only when a new group is created."
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
            "Read exactly these groups (1-20 IDs, any group type) instead of listing. "
            "Mutually exclusive with name, next_token and sort_order; unreadable IDs are "
            "reported in errors."
        ),
    )
    name: str | None = Field(
        None, max_length=64, description="Fuzzy name filter (substring match by the provider)."
    )
    group_type: Literal["AIGC", "LivenessFace"] = Field(
        "AIGC",
        description=(
            "Group type to list (the provider requires one): 'AIGC' (virtual portrait, "
            "default) or 'LivenessFace' (verified real person). Ignored with group_ids."
        ),
    )
    max_results: int = Field(
        20, ge=1, le=100, description="Page size (1-100). Ignored with group_ids."
    )
    next_token: str | None = Field(
        None, max_length=4096, description="Pagination token from a previous call's next_token."
    )
    sort_order: Literal["Asc", "Desc"] | None = Field(
        None, description="Sort by creation time: 'Asc' or 'Desc'. Provider default when omitted."
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
        None, description="Pass to the next call to fetch more; null when there are no more."
    )
    errors: list[AssetLookupError] = Field(
        default_factory=list, description="group_ids that could not be read; empty when listing."
    )


# --- Assets --------------------------------------------------------------------


class AssetSource(BaseModel):
    """One file to register as an asset: a public HTTPS URL or a media_upload object key."""

    url: str | None = Field(
        None,
        description=(
            "Publicly reachable HTTPS URL of the file (the provider downloads it). Mutually "
            "exclusive with object_key."
        ),
    )
    object_key: str | None = Field(
        None,
        description=(
            "Object key from media_upload; a presigned URL is minted automatically. Mutually "
            "exclusive with url."
        ),
    )
    asset_type: Literal["Image", "Video", "Audio"] | None = Field(
        None,
        description=(
            "Asset type. Inferred from the file extension or object key when omitted. "
            "Image: jpeg/png/webp/bmp/tiff/gif/heic, 300-6000 px, W/H 0.4-2.5, < 30 MB. "
            "Video: mp4/mov, 2-30 s, 24-60 fps, <= 200 MB. Audio: wav/mp3, 2-30 s, <= 15 MB."
        ),
    )
    name: str | None = Field(
        None,
        max_length=64,
        description="Asset name for search in ark_asset_list. The model never sees it.",
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
        description=(
            "Files of ONE subject to add to one group (1-20). Different people or characters "
            "belong in different groups."
        ),
    )
    group_id: str | None = Field(
        None,
        pattern=_GROUP_ID_PATTERN,
        description=(
            "Target group ID (AIGC or LivenessFace). Mutually exclusive with subject. Real "
            "people must use the group_id returned by ark_asset_verification_result."
        ),
    )
    subject: str | None = Field(
        None,
        min_length=1,
        max_length=64,
        description=(
            "AIGC subject name: reuse the single AIGC group with this exact name, or create "
            "it within one server event loop. Mutually exclusive with group_id; use group_id "
            "across separate servers to avoid concurrent duplicate creation."
        ),
    )
    skip_moderation: bool = Field(
        False,
        description=(
            "Skip most non-baseline content pre-filter checks (Moderation.Strategy=Skip). "
            "Works only after content pre-filter is turned off in the ModelArk console."
        ),
    )
    wait_until_active: bool = Field(
        True,
        description="Poll GetAsset until each asset is Active or Failed (bounded by wait_timeout_seconds).",
    )
    wait_timeout_seconds: int = Field(
        120, ge=5, le=900, description="Maximum seconds to wait when wait_until_active is true."
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
    asset_id: str | None = Field(None, description="Created asset ID, when accepted.")
    asset_uri: str | None = Field(
        None, description="'asset://<asset_id>' reference for Seedance, when accepted."
    )
    status: str | None = Field(
        None,
        description="Last observed status: Processing, Active, or Failed (null if not created).",
    )
    error: str | None = Field(None, description="Why this source failed, if it did.")


class AssetCreateOutput(BaseModel):
    """Output for ``ark_asset_create``."""

    group_id: str = Field(..., description="Group the assets were added to.")
    group_created: bool = Field(
        False, description="True when the subject's group was created by this call."
    )
    group_created_empty: bool = Field(
        False,
        description=(
            "True when this call created the subject's group but every source failed, leaving "
            "it empty; delete it with ark_asset_delete(group_id=...) if it is not needed."
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
            "Read exactly these assets (1-20 IDs or 'asset://<id>' references) instead of "
            "listing. Mutually exclusive with the filters; unreadable IDs are reported in errors."
        ),
    )
    group_ids: list[GroupId] | None = Field(
        None, max_length=100, description="Only assets in these groups."
    )
    group_type: Literal["AIGC", "LivenessFace"] = Field(
        "AIGC",
        description=(
            "Only assets in groups of this type (the provider requires one): 'AIGC' "
            "(default) or 'LivenessFace'. Ignored with asset_ids."
        ),
    )
    statuses: list[Literal["Processing", "Active", "Failed"]] | None = Field(
        None, description="Only assets with these statuses."
    )
    name: str | None = Field(None, max_length=64, description="Fuzzy asset-name filter.")
    max_results: int = Field(
        20, ge=1, le=100, description="Page size (1-100). Ignored with asset_ids."
    )
    next_token: str | None = Field(
        None, max_length=4096, description="Pagination token from a previous call's next_token."
    )
    sort_order: Literal["Asc", "Desc"] | None = Field(
        None, description="Sort by creation time: 'Asc' or 'Desc'. Provider default when omitted."
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
        None, description="Pass to the next call to fetch more; null when there are no more."
    )
    errors: list[AssetLookupError] = Field(
        default_factory=list, description="asset_ids that could not be read; empty when listing."
    )


class _AssetOrGroupTarget(BaseModel):
    asset_id: AssetId | None = Field(
        None,
        description="Asset ID or 'asset://<id>' reference. Exactly one of asset_id or group_id.",
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

    confirm: Literal[True] = Field(
        ..., description="Must be true. Deleting an asset or group is irreversible."
    )
    cascade: bool = Field(
        False,
        description=(
            "Group deletes only: also delete the group's assets. A non-empty group is refused "
            "unless this is true."
        ),
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
            "HTTPS page the person is sent to after verification; the provider appends "
            "bytedToken and resultCode (10000 = success). Defaults to "
            "BYTEPLUS_MODELARK_ASSET_VERIFY_CALLBACK_URL."
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
            "Verification page link. Contains temporary credentials: share it only with the "
            "person being verified. It becomes invalid after a failed attempt."
        ),
    )
    verification_token: str = Field(
        ...,
        description="Token for ark_asset_verification_result. Valid for about 30 minutes.",
    )
    expires_at: str = Field(..., description="Approximate token expiry (ISO-8601, UTC).")
    next_step: str = Field(..., description="What to do next.")


class VerificationResultInput(BaseModel):
    """Input for ``ark_asset_verification_result``."""

    verification_token: str = Field(
        ...,
        min_length=8,
        max_length=512,
        description="verification_token from ark_asset_verification_start (bytedToken).",
    )
    wait_seconds: int = Field(
        0,
        ge=0,
        le=600,
        description="Keep polling up to this many seconds for the person to finish (0 = one check).",
    )
    project_name: str | None = project_name_field()


class VerificationResultOutput(BaseModel):
    """Output for ``ark_asset_verification_result``."""

    status: Literal["verified", "pending"] = Field(
        ..., description="'verified' once a group exists; 'pending' when not finished yet."
    )
    group_id: str | None = Field(
        None,
        description=(
            "The verified person's LivenessFace group ID. Upload only that person's media to "
            "it with ark_asset_create(group_id=...)."
        ),
    )
    message: str = Field(..., description="Human-readable status detail.")
