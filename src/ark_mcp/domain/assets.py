"""Private asset library domain models and ``asset://`` URI helpers.

The ModelArk private asset library (Dreamina Seedance Advanced Creation
Rights) stores trusted media in *asset groups* — one group per subject (a
verified real person, a virtual character, a product). Each file is an
*asset* referenced in generation requests as ``asset://<asset_id>``.

These models appear in MCP tool output schemas, so every field is described.
See ``plans/PLAN_MODELARK_ASSET_LIBRARY.md``.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

ASSET_URI_PREFIX = "asset://"
# Asset IDs look like ``asset-20260318035710-kctzf``. Copyright-IP and preset
# character IDs share the scheme; accept any conservative token so those work
# too, while rejecting paths, whitespace, and URL syntax.
_ASSET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class AssetUriError(ValueError):
    """Raised for a malformed ``asset://`` reference."""


def is_asset_uri(value: object) -> bool:
    """Return True when ``value`` is a string using the ``asset://`` scheme."""
    return isinstance(value, str) and value[: len(ASSET_URI_PREFIX)].lower() == ASSET_URI_PREFIX


def validate_asset_id(asset_id: str) -> str:
    """Validate a bare asset ID and return it unchanged."""
    if not _ASSET_ID_PATTERN.match(asset_id):
        raise AssetUriError(
            "Invalid asset ID. Expected an ID such as 'asset-20260318035710-kctzf' "
            "(letters, digits, '-' and '_' only)."
        )
    return asset_id


def parse_asset_uri(value: str) -> str:
    """Return the asset ID from ``asset://<asset_id>``, validating its shape."""
    if not is_asset_uri(value):
        raise AssetUriError("Expected an asset reference of the form 'asset://<asset_id>'.")
    return validate_asset_id(value[len(ASSET_URI_PREFIX) :])


def asset_uri(asset_id: str) -> str:
    """Build the ``asset://<asset_id>`` reference used in generation requests."""
    return f"{ASSET_URI_PREFIX}{validate_asset_id(asset_id)}"


class AssetGroup(BaseModel):
    """One asset group: all assets of a single subject."""

    group_id: str = Field(..., description="Asset group ID (e.g. 'group-20260318033332-7vw4m').")
    name: str = Field("", description="Group name (used for search; not seen by the model).")
    description: str = Field("", description="Free-text group description.")
    group_type: str = Field(
        "",
        description=(
            "Group type: 'AIGC' (virtual portrait / non-real character, created by API) or "
            "'LivenessFace' (verified real person, created by real-person verification)."
        ),
    )
    project_name: str = Field(
        "", description="ModelArk project that owns the group; assets only work in that project."
    )
    created_at: str | None = Field(None, description="Creation time (ISO-8601, provider value).")
    updated_at: str | None = Field(None, description="Last update time (ISO-8601, provider value).")

    @classmethod
    def from_provider(cls, item: dict[str, Any]) -> AssetGroup:
        return cls(
            group_id=str(item.get("Id") or ""),
            name=str(item.get("Name") or ""),
            description=str(item.get("Description") or ""),
            group_type=str(item.get("GroupType") or ""),
            project_name=str(item.get("ProjectName") or ""),
            created_at=item.get("CreateTime"),
            updated_at=item.get("UpdateTime"),
        )


class Asset(BaseModel):
    """One trusted media file in the private asset library."""

    asset_id: str = Field(..., description="Asset ID (e.g. 'asset-20260318035710-kctzf').")
    asset_uri: str = Field(
        ...,
        description=(
            "Durable reference for generation requests: 'asset://<asset_id>'. Use it as an "
            "image/video/audio URL in Seedance tools. In prompts refer to it by position "
            "('Image 1', 'Video 1'), never by ID."
        ),
    )
    name: str = Field("", description="Asset name (search only; not seen by the model).")
    group_id: str = Field("", description="Asset group the asset belongs to.")
    asset_type: str = Field("", description="Asset type: 'Image', 'Video', or 'Audio'.")
    status: str = Field(
        "",
        description=(
            "Processing status: 'Processing' (queued/preprocessing, not usable yet), "
            "'Active' (usable in generation), or 'Failed' (preprocessing or review failed)."
        ),
    )
    project_name: str = Field("", description="ModelArk project that owns the asset.")
    moderation_strategy: str | None = Field(
        None, description="Content pre-filter strategy: 'Default' or 'Skip'."
    )
    preview_url: str | None = Field(
        None,
        description=(
            "Temporary presigned download URL for the stored file (expires after about 12 "
            "hours). For viewing only; always use asset_uri in generation requests."
        ),
    )
    created_at: str | None = Field(None, description="Creation time (ISO-8601, provider value).")
    updated_at: str | None = Field(None, description="Last update time (ISO-8601, provider value).")
    last_inference_time: str | None = Field(
        None,
        description=(
            "When the asset was last used to submit a generation task. Absent means no "
            "recorded use since 2026-07-24, not necessarily never used."
        ),
    )

    @classmethod
    def from_provider(cls, item: dict[str, Any]) -> Asset:
        asset_id = str(item.get("Id") or "")
        moderation = item.get("Moderation")
        return cls(
            asset_id=asset_id,
            asset_uri=f"{ASSET_URI_PREFIX}{asset_id}",
            name=str(item.get("Name") or ""),
            group_id=str(item.get("GroupId") or ""),
            asset_type=str(item.get("AssetType") or ""),
            status=str(item.get("Status") or ""),
            project_name=str(item.get("ProjectName") or ""),
            moderation_strategy=(
                str(moderation.get("Strategy")) if isinstance(moderation, dict) else None
            ),
            preview_url=item.get("URL") or None,
            created_at=item.get("CreateTime"),
            updated_at=item.get("UpdateTime"),
            last_inference_time=item.get("LastInferenceTime"),
        )
