"""Input and output models shared by the ``ark_asset_*`` tools.

All fields carry descriptions because these models are MCP tool schemas.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ark_mcp.domain.assets import Asset, AssetGroup, validate_asset_id
from ark_mcp.tools.media_presign import validate_object_key

_PROJECT_DESCRIPTION = (
    "ModelArk project name. Defaults to BYTEPLUS_MODELARK_PROJECT_NAME ('default'). Assets "
    "only work with inference endpoints in the same project; keep one project throughout."
)
_GROUP_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"


def project_name_field() -> Any:
    return Field(None, min_length=1, max_length=64, description=_PROJECT_DESCRIPTION)


# --- Groups --------------------------------------------------------------------


class AssetGroupCreateInput(BaseModel):
    """Input for ``ark_asset_group_create``."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Group name, typically the subject (character or product) it holds.",
    )
    description: str | None = Field(
        None, max_length=300, description="Optional free-text description of the subject."
    )
    project_name: str | None = project_name_field()


class AssetGroupEnsureInput(BaseModel):
    """Input for ``ark_asset_group_ensure``."""

    subject: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description=(
            "Exact subject name. Reuses the single AIGC group with exactly this name, or "
            "creates it. Use one subject per character/product so its assets stay together."
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


class AssetGroupGetInput(BaseModel):
    """Input for ``ark_asset_group_get``."""

    group_id: str = Field(..., pattern=_GROUP_ID_PATTERN, description="Asset group ID.")
    project_name: str | None = project_name_field()


class AssetGroupListInput(BaseModel):
    """Input for ``ark_asset_group_list``."""

    name: str | None = Field(
        None, max_length=64, description="Fuzzy name filter (substring match by the provider)."
    )
    group_ids: list[str] | None = Field(
        None, max_length=100, description="Only return these group IDs."
    )
    group_type: Literal["AIGC", "LivenessFace"] = Field(
        "AIGC",
        description=(
            "Group type to list (the provider requires one): 'AIGC' (virtual portrait, "
            "default) or 'LivenessFace' (verified real person)."
        ),
    )
    max_results: int = Field(20, ge=1, le=100, description="Page size (1-100).")
    next_token: str | None = Field(
        None, max_length=4096, description="Pagination token from a previous call's next_token."
    )
    sort_order: Literal["Asc", "Desc"] | None = Field(
        None, description="Sort by creation time: 'Asc' or 'Desc'. Provider default when omitted."
    )
    project_name: str | None = project_name_field()


class AssetGroupPage(BaseModel):
    """Output for ``ark_asset_group_list``."""

    groups: list[AssetGroup] = Field(..., description="Asset groups on this page.")
    next_token: str | None = Field(
        None, description="Pass to the next call to fetch more; null when there are no more."
    )


class AssetGroupUpdateInput(BaseModel):
    """Input for ``ark_asset_group_update``."""

    group_id: str = Field(..., pattern=_GROUP_ID_PATTERN, description="Asset group ID.")
    name: str | None = Field(None, min_length=1, max_length=64, description="New group name.")
    description: str | None = Field(None, max_length=300, description="New group description.")
    project_name: str | None = project_name_field()

    @model_validator(mode="after")
    def _require_change(self) -> AssetGroupUpdateInput:
        if self.name is None and self.description is None:
            raise ValueError("Provide name and/or description to update.")
        return self


class AssetGroupDeleteInput(BaseModel):
    """Input for ``ark_asset_group_delete``."""

    group_id: str = Field(..., pattern=_GROUP_ID_PATTERN, description="Asset group ID to delete.")
    confirm: Literal[True] = Field(
        ...,
        description=(
            "Must be true. Deleting a group is irreversible and removes its assets from use."
        ),
    )
    project_name: str | None = project_name_field()


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
            "it. Mutually exclusive with group_id."
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
    items: list[AssetCreateItem] = Field(..., description="One result per source, in order.")
    all_active: bool = Field(..., description="True when every source ended Active.")


class AssetGetInput(BaseModel):
    """Input for ``ark_asset_get``."""

    asset_id: str = Field(..., description="Asset ID, or its 'asset://<asset_id>' reference.")
    project_name: str | None = project_name_field()

    @field_validator("asset_id", mode="before")
    @classmethod
    def _strip_scheme(cls, value: object) -> object:
        if isinstance(value, str) and value.lower().startswith("asset://"):
            return value[len("asset://") :]
        return value

    @field_validator("asset_id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return validate_asset_id(value)


class AssetResult(BaseModel):
    """Output for tools that return one asset."""

    asset: Asset = Field(..., description="The asset.")


class AssetListInput(BaseModel):
    """Input for ``ark_asset_list``."""

    group_ids: list[str] | None = Field(
        None, max_length=100, description="Only assets in these groups."
    )
    group_type: Literal["AIGC", "LivenessFace"] = Field(
        "AIGC",
        description=(
            "Only assets in groups of this type (the provider requires one): 'AIGC' "
            "(default) or 'LivenessFace'."
        ),
    )
    statuses: list[Literal["Processing", "Active", "Failed"]] | None = Field(
        None, description="Only assets with these statuses."
    )
    name: str | None = Field(None, max_length=64, description="Fuzzy asset-name filter.")
    max_results: int = Field(20, ge=1, le=100, description="Page size (1-100).")
    next_token: str | None = Field(
        None, max_length=4096, description="Pagination token from a previous call's next_token."
    )
    sort_order: Literal["Asc", "Desc"] | None = Field(
        None, description="Sort by creation time: 'Asc' or 'Desc'. Provider default when omitted."
    )
    project_name: str | None = project_name_field()


class AssetPage(BaseModel):
    """Output for ``ark_asset_list``."""

    assets: list[Asset] = Field(..., description="Assets on this page.")
    next_token: str | None = Field(
        None, description="Pass to the next call to fetch more; null when there are no more."
    )


class AssetUpdateInput(AssetGetInput):
    """Input for ``ark_asset_update``."""

    name: str = Field(..., min_length=1, max_length=64, description="New asset name.")


class AssetDeleteInput(AssetGetInput):
    """Input for ``ark_asset_delete``."""

    confirm: Literal[True] = Field(
        ..., description="Must be true. Deleting an asset is irreversible."
    )


class DeleteOutput(BaseModel):
    """Output for the delete tools."""

    id: str = Field(..., description="ID of the deleted asset or group.")
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
